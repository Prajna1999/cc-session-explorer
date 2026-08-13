"""``cc-cloud`` — CLI to sync local Claude Code data to the CC Explorer Cloud.

Commands
--------
login --url URL          Store the API base URL and prompt for an API token.
sync [--project PATH]    Upload sessions and/or commit context for a repo.
projects add …           Create a project (team + repo URL) on the server.
status [--project PATH]  Show what a sync would upload.

Reuses ``commit_context.parser.parse_session`` (single source of truth for transcript
parsing) and ``commit_context.materialize`` (repo root / project slug helpers).
Incremental sync is driven by a local cursor file under ``~/.config/cc-cloud/cursors/``;
the server mirrors it in ``sync_state`` per device.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

from commit_context.materialize import commits_dir, project_slug, repo_root
from commit_context.parser import parse_session

from .ingest import commit_from_bundle
from .schemas import (
    CommitsIngestIn,
    CommitIn,
    DeviceIn,
    SessionIn,
    SessionsIngestIn,
    SubagentIn,
    ToolResultIn,
)

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cc-cloud"
CONFIG_FILE = CONFIG_DIR / "config.json"
CURSOR_DIR = CONFIG_DIR / "cursors"

SESSION_CHUNK = 10
COMMIT_CHUNK = 50
MAX_TOOL_RESULT_BYTES = 512 * 1024


def _serialize_entries(entries: list) -> list[dict]:
    """parse_session returns datetimes for ``ts``; serialize to ISO strings."""
    out = []
    for i, e in enumerate(entries):
        e = dict(e)
        e["ts"] = e["ts"].isoformat() if e.get("ts") else None
        out.append({"seq": i, **e})
    return out


# ---------------------------------------------------------------------------
# Config / client
# ---------------------------------------------------------------------------


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def api_url() -> str:
    return os.environ.get("CC_CLOUD_API_URL") or load_config().get("api_url") or "http://localhost:8000"


def client() -> httpx.Client:
    cfg = load_config()
    token = os.environ.get("CC_CLOUD_TOKEN") or cfg.get("token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=api_url(), headers=headers, timeout=60.0)


def load_cursor(slug: str) -> dict:
    path = CURSOR_DIR / f"{slug}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"sessions": {}, "commits": []}


def save_cursor(slug: str, cursor: dict) -> None:
    CURSOR_DIR.mkdir(parents=True, exist_ok=True)
    (CURSOR_DIR / f"{slug}.json").write_text(json.dumps(cursor, indent=2))


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def run_git(cwd: Path, *args: str) -> list[str]:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
        return out.stdout.splitlines() if out.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        return []


def remote_url(root: Path) -> str | None:
    lines = run_git(root, "remote", "get-url", "origin")
    return lines[0] if lines else None


def git_log_map(root: Path) -> dict[str, dict]:
    """sha -> {subject, author_name, author_email, authored_at} for reachable commits."""
    out = run_git(root, "log", "--all", "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%s", "-n", "500")
    result: dict[str, dict] = {}
    for line in out:
        parts = line.split("\x1f")
        if len(parts) == 5:
            result[parts[0]] = {
                "author_name": parts[1],
                "author_email": parts[2],
                "authored_at": parts[3],
                "subject": parts[4],
            }
    return result


# ---------------------------------------------------------------------------
# Session upload
# ---------------------------------------------------------------------------


def _subagent_payloads(session_dir: Path) -> tuple[list[SubagentIn], bool]:
    agents: list[SubagentIn] = []
    sub_dir = session_dir / "subagents"
    if not sub_dir.is_dir():
        return agents, False
    for meta_file in sorted(sub_dir.glob("agent-*.meta.json")):
        try:
            meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            meta = {}
        jsonl = meta_file.with_name(meta_file.name.replace(".meta.json", ".jsonl"))
        if not jsonl.exists():
            continue
        entries, _ = parse_session(jsonl)
        agents.append(
            SubagentIn(
                external_id=meta_file.name.removesuffix(".meta.json"),
                agent_type=str(meta.get("agentType", "?")),
                description=str(meta.get("description", "")),
                size_bytes=jsonl.stat().st_size,
                entries=_serialize_entries(entries),
            )
        )
    return agents, True


def _tool_result_payloads(session_dir: Path) -> tuple[list[ToolResultIn], bool]:
    results: list[ToolResultIn] = []
    tr_dir = session_dir / "tool-results"
    if not tr_dir.is_dir():
        return results, False
    for f in sorted(tr_dir.iterdir()):
        if not f.is_file():
            continue
        content = f.read_text(errors="replace")[:MAX_TOOL_RESULT_BYTES]
        results.append(ToolResultIn(name=f.name, size_bytes=f.stat().st_size, content=content))
    return results, True


def _session_payload(path: Path, stem: str) -> SessionIn:
    entries, meta = parse_session(path)
    session_dir = path.parent / stem
    subagents, has_sub = _subagent_payloads(session_dir)
    tool_results, has_tr = _tool_result_payloads(session_dir)
    return SessionIn(
        external_id=stem,
        title=meta["title"],
        cwd=meta["cwd"],
        branch=meta["branches"][-1] if meta["branches"] else None,
        version=meta["version"],
        started_at=meta["first"].isoformat() if meta["first"] else None,
        ended_at=meta["last"].isoformat() if meta["last"] else None,
        models=meta["models"],
        branches=meta["branches"],
        prompts=meta["prompts"],
        tool_calls=meta["tool_calls"],
        tool_counts=meta["tool_counts"],
        thinking=meta["thinking"],
        responses=meta["responses"],
        size_bytes=path.stat().st_size,
        has_subagents=has_sub,
        has_tool_results=has_tr,
        entries=_serialize_entries(entries),
        subagents=subagents,
        tool_results=tool_results,
    )


def upload_sessions(project_slug_server: str, project_dir: Path, force: bool = False) -> dict:
    cursor = load_cursor(project_slug_server)
    files = sorted(project_dir.glob("*.jsonl"))
    pending = []
    for f in files:
        mtime = f.stat().st_mtime
        if not force and cursor["sessions"].get(f.stem, 0) >= mtime:
            continue
        pending.append(f)

    if not pending:
        return {"sessions_uploaded": 0, "entries_uploaded": 0}

    print(f"  uploading {len(pending)} session(s)…")
    total_entries = 0
    with client() as c:
        for i in range(0, len(pending), SESSION_CHUNK):
            chunk = pending[i : i + SESSION_CHUNK]
            payload = SessionsIngestIn(
                project=project_slug_server,
                device=DeviceIn(hostname=os.uname().nodename, os=sys.platform),
                sessions=[_session_payload(f, f.stem) for f in chunk],
            )
            resp = c.post("/api/ingest/sessions", json=payload.model_dump(mode="json"))
            resp.raise_for_status()
            result = resp.json()
            total_entries += result["entries_added"]
            for f in chunk:
                cursor["sessions"][f.stem] = f.stat().st_mtime
    save_cursor(project_slug_server, cursor)
    return {"sessions_uploaded": len(pending), "entries_uploaded": total_entries}


# ---------------------------------------------------------------------------
# Commit upload
# ---------------------------------------------------------------------------


def upload_commits(project_slug_server: str, root: Path, force: bool = False) -> dict:
    cursor = load_cursor(project_slug_server)
    synced: set[str] = set(cursor["commits"])
    bundles_dir = commits_dir(root)

    commits: list[CommitIn] = []
    meta_map = git_log_map(root)
    for bundle_file in sorted(bundles_dir.glob("*.json")):
        sha = bundle_file.stem
        if not force and sha in synced:
            continue
        try:
            bundle = json.loads(bundle_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        commit = commit_from_bundle(None, None, None, bundle)  # pure transformation
        meta = meta_map.get(sha, {})
        if meta:
            commit.subject = meta["subject"]
            commit.author_name = meta["author_name"]
            commit.author_email = meta["author_email"]
            commit.authored_at = meta["authored_at"]
        commits.append(commit)

    if not commits:
        return {"commits_uploaded": 0, "contexts_uploaded": 0}

    print(f"  uploading {len(commits)} commit context(s)…")
    contexts = 0
    with client() as c:
        for i in range(0, len(commits), COMMIT_CHUNK):
            chunk = commits[i : i + COMMIT_CHUNK]
            payload = CommitsIngestIn(project=project_slug_server, commits=chunk)
            resp = c.post("/api/ingest/commits", json=payload.model_dump(mode="json"))
            resp.raise_for_status()
            result = resp.json()
            contexts += result["contexts_added"]
            for commit in chunk:
                synced.add(commit.sha)
    cursor["commits"] = sorted(synced)
    save_cursor(project_slug_server, cursor)
    return {"commits_uploaded": len(commits), "contexts_uploaded": contexts}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_login(args: argparse.Namespace) -> int:
    token = args.token or input("API token: ").strip()
    if not token:
        print("error: no token given", file=sys.stderr)
        return 1
    save_config({"api_url": args.url.rstrip("/"), "token": token})
    print(f"stored credentials for {args.url}")
    return 0


def _resolve_slug(c: httpx.Client, root: Path) -> str | None:
    url = remote_url(root)
    if not url:
        print("error: no 'origin' remote configured for this repo", file=sys.stderr)
        return None
    resp = c.get("/api/projects/resolve", params={"repo_url": url})
    if resp.status_code == 404:
        print(f"error: no cloud project for repo '{url}' — create one first:", file=sys.stderr)
        print(f"  cc-cloud projects add --team <slug> --repo '{url}' --name <name>", file=sys.stderr)
        return None
    resp.raise_for_status()
    return resp.json()["slug"]


def cmd_sync(args: argparse.Namespace) -> int:
    root = repo_root(args.project and Path(args.project).resolve() or None)
    slug = project_slug(root)
    print(f"repo:   {root}")
    print(f"local:  ~/.claude/projects/{slug}")

    with client() as c:
        server_slug = _resolve_slug(c, root)
        if server_slug is None:
            return 1

        total_sessions = total_entries = total_commits = total_contexts = 0
        if args.sessions:
            project_dir = Path.home() / ".claude" / "projects" / slug
            if not project_dir.is_dir():
                print(f"  no local sessions at {project_dir}")
            else:
                r = upload_sessions(server_slug, project_dir, force=args.force)
                total_sessions += r["sessions_uploaded"]
                total_entries += r["entries_uploaded"]
        if args.commits:
            r = upload_commits(server_slug, root, force=args.force)
            total_commits += r["commits_uploaded"]
            total_contexts += r["contexts_uploaded"]

        # Mirror cursors server-side (best-effort; local cursor is authoritative).
        for kind, marker in (("sessions", str(datetime.now().isoformat())), ("commits", str(datetime.now().isoformat()))):
            c.post(
                "/api/ingest/cursor",
                json={"project": server_slug, "kind": kind, "device": os.uname().nodename, "last_marker": marker},
            )

    print(f"done: {total_sessions} sessions, {total_entries} entries, "
          f"{total_commits} commits, {total_contexts} contexts")
    return 0


def cmd_me(_args: argparse.Namespace) -> int:
    with client() as c:
        resp = c.get("/api/me")
        resp.raise_for_status()
        data = resp.json()
    user = data["user"]
    print(f"user: {user['email']} ({user.get('name') or 'no name'})")
    for t in data["teams"]:
        print(f"  team {t['slug']!r} ({t['name']}) — role {t['role']}, id {t['id']}")
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    with client() as c:
        me = c.get("/api/me")
        me.raise_for_status()
        data = me.json()
        team = next((t for t in data["teams"] if t["slug"] == args.team), None)
        if team is None:
            print(f"error: no team with slug '{args.team}' — run 'cc-cloud me' to list your teams", file=sys.stderr)
            return 1
        resp = c.post(
            "/api/projects",
            json={"team_id": team["id"], "name": args.name, "repo_url": args.repo,
                  "slug": args.slug, "default_branch": args.branch},
        )
        if resp.status_code == 409:
            print(resp.json().get("detail", "project already exists"))
            return 0
        resp.raise_for_status()
        proj = resp.json()
        print(f"created project '{proj['slug']}' (id {proj['id']})")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = repo_root(args.project and Path(args.project).resolve() or None)
    slug = project_slug(root)
    project_dir = Path.home() / ".claude" / "projects" / slug
    cursor = load_cursor(slug)

    pending_sessions = 0
    if project_dir.is_dir():
        for f in sorted(project_dir.glob("*.jsonl")):
            if cursor["sessions"].get(f.stem, 0) < f.stat().st_mtime:
                pending_sessions += 1
    pending_commits = len(
        [p for p in commits_dir(root).glob("*.json") if p.stem not in set(cursor["commits"])]
    )
    print(f"repo: {root}")
    print(f"pending sessions: {pending_sessions}")
    print(f"pending commit contexts: {pending_commits}")
    print(f"run 'cc-cloud sync' to upload")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cc-cloud", description="Sync Claude Code data to the CC Explorer Cloud")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="store API URL + token")
    p_login.add_argument("--url", default=os.environ.get("CC_CLOUD_API_URL", "http://localhost:8000"))
    p_login.add_argument("--token", default=None)
    p_login.set_defaults(func=cmd_login)

    p_me = sub.add_parser("me", help="show your user + teams")
    p_me.set_defaults(func=cmd_me)

    p_sync = sub.add_parser("sync", help="upload sessions + commit context")
    p_sync.add_argument("--project", default=None, help="path to the repo (default: cwd)")
    p_sync.add_argument("--sessions", action="store_true", help="upload full session transcripts")
    p_sync.add_argument("--commits", action="store_true", help="upload commit context bundles")
    p_sync.add_argument("--force", action="store_true", help="ignore cursors and re-upload everything")
    p_sync.set_defaults(func=cmd_sync)

    p_proj = sub.add_parser("projects", help="manage cloud projects")
    p_proj.add_argument("action", choices=["add"])
    p_proj.add_argument("--team", required=True, help="team slug (see 'cc-cloud me')")
    p_proj.add_argument("--repo", required=True, help="repo URL (origin remote)")
    p_proj.add_argument("--name", required=True, help="display name")
    p_proj.add_argument("--slug", default=None, help="optional URL slug")
    p_proj.add_argument("--branch", default=None, help="default branch")
    p_proj.set_defaults(func=cmd_projects)

    p_status = sub.add_parser("status", help="show what a sync would upload")
    p_status.add_argument("--project", default=None)
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
