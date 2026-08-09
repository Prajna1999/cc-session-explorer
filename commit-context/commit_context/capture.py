import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .parser import parse_session

NOTES_REF = "refs/notes/claude-context"


def _run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _iso(ts):
    return ts.isoformat() if ts else None


def cursor_path(repo_root: Path, session_id: str) -> Path:
    d = repo_root / ".git" / "claude-commit-context"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{session_id}.json"


def load_cursor(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_sha": None, "last_ts": None}


def compute_slice_meta(entries: list[dict]) -> dict:
    meta = {"models": [], "branches": [], "first": None, "last": None,
            "prompts": 0, "tool_calls": 0, "tool_counts": {}, "thinking": 0, "responses": 0}
    for e in entries:
        ts = e.get("ts")
        if ts:
            meta["first"] = meta["first"] or ts
            meta["last"] = ts
        kind = e.get("kind")
        if kind == "branch":
            if e["text"] not in meta["branches"]:
                meta["branches"].append(e["text"])
        elif kind == "user":
            meta["prompts"] += 1
        elif kind == "assistant":
            meta["responses"] += 1
            if e.get("model") and e["model"] not in meta["models"]:
                meta["models"].append(e["model"])
        elif kind == "thinking":
            meta["thinking"] += 1
        elif kind == "tool":
            meta["tool_calls"] += 1
            meta["tool_counts"][e["name"]] = meta["tool_counts"].get(e["name"], 0) + 1
    meta["tool_counts"] = dict(sorted(meta["tool_counts"].items(), key=lambda kv: -kv[1]))
    return meta


def run(argv=None) -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    command = (payload.get("tool_input") or {}).get("command", "") or ""
    if "git commit" not in command:
        return 0  # dominant path: every non-commit Bash call must stay cheap

    cwd = payload.get("cwd")
    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    if not (cwd and session_id and transcript_path):
        return 0

    root = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if root.returncode != 0:
        return 0
    repo_root = Path(root.stdout.strip())

    sha_result = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    if sha_result.returncode != 0:
        return 0
    new_sha = sha_result.stdout.strip()

    cpath = cursor_path(repo_root, session_id)
    cursor = load_cursor(cpath)
    if cursor.get("last_sha") == new_sha:
        return 0  # commit no-op'd, failed, or nothing was staged

    transcript = Path(transcript_path)
    if not transcript.exists():
        return 0
    entries, _whole_session_meta = parse_session(transcript)

    last_ts_raw = cursor.get("last_ts")
    if last_ts_raw:
        last_ts = datetime.fromisoformat(last_ts_raw)
        entries = [e for e in entries if e.get("ts") and e["ts"] > last_ts]

    slice_meta = compute_slice_meta(entries)
    branch_names = slice_meta["branches"]

    def serialize_entry(e):
        e = dict(e)
        e["ts"] = _iso(e.get("ts"))
        return e

    bundle_entries = [serialize_entry(e) for e in entries]
    bundle = {
        "schema_version": 1,
        "commit": new_sha,
        "session_id": session_id,
        "branch": branch_names[-1] if branch_names else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "entries": bundle_entries,
        "meta": {
            "models": slice_meta["models"],
            "branches": slice_meta["branches"],
            "first": _iso(slice_meta["first"]),
            "last": _iso(slice_meta["last"]),
            "prompts": slice_meta["prompts"],
            "tool_calls": slice_meta["tool_calls"],
            "tool_counts": slice_meta["tool_counts"],
            "thinking": slice_meta["thinking"],
            "responses": slice_meta["responses"],
        },
    }

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(bundle, tmp)
        tmp_path = tmp.name
    try:
        _run(["git", "notes", f"--ref={NOTES_REF}", "add", "-f", "-F", tmp_path, new_sha], cwd=repo_root)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    new_last_ts = _iso(slice_meta["last"]) or last_ts_raw
    cpath.write_text(json.dumps({"last_sha": new_sha, "last_ts": new_last_ts}))
    return 0
