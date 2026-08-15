"""Behavior tests for commit-context capture (run from ``cli/``)::

    uv run python tests/capture_test.py

Covers the native ``post-commit`` hook (context for commits made outside Claude
Code — GUI/plain terminal), the Claude Code ``PostToolUse`` capture path, and
idempotency between the two. Uses a scratch repo with ``HOME`` redirected so the
fake ``~/.claude/projects/<slug>`` session tree never touches the real one.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CLI_ROOT))  # allow `python tests/capture_test.py` without uv

from commit_context.capture import last_captured_ts  # noqa: E402
from commit_context.materialize import project_slug  # noqa: E402

NOTES_REF = "refs/notes/claude-context"

failures: list[str] = []
total_checks = 0


def check(name: str, cond: bool, detail: str = ""):
    global total_checks
    total_checks += 1
    print(("  ok  " if cond else "FAIL  ") + name + ("" if cond else f"  ({detail})"))
    if not cond:
        failures.append(name)


def _run(args, cwd, env, input=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, input=input)


def session_lines(ts_base: str) -> str:
    """A tiny valid Claude Code jsonl transcript (user -> assistant -> tool)."""
    return "\n".join([
        json.dumps({"type": "user", "timestamp": f"{ts_base}:00+00:00",
                    "message": {"content": "work on the explorer"}}),
        json.dumps({"type": "assistant", "timestamp": f"{ts_base}:05+00:00",
                    "message": {"model": "claude-sonnet-4-20250514", "content": [
                        {"type": "text", "text": "Let me check."},
                        {"type": "tool_use", "id": "toolu_1", "name": "Bash",
                         "input": {"command": "git status"}},
                    ]}}),
        json.dumps({"type": "system", "timestamp": f"{ts_base}:07+00:00",
                    "subtype": "turn_duration", "durationMs": 1000, "messageCount": 2}),
    ]) + "\n"


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="cc-context-test-"))
    home = base / "home"
    home.mkdir()
    repo = base / "repo"
    repo.mkdir()

    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    venv_bin = Path(sys.executable).parent  # uv run's venv has the cc-commit-context script
    env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(CLI_ROOT)

    try:
        r = _run(["git", "init", "-q", "-b", "main"], repo, env)
        assert r.returncode == 0, r.stderr
        for k, v in [("user.email", "alice@acme.dev"), ("user.name", "Alice"),
                     ("commit.gpgsign", "false")]:
            _run(["git", "config", k, v], repo, env)

        # --- install ---------------------------------------------------------
        r = _run([sys.executable, "-m", "commit_context.cli", "install"], repo, env)
        check("install runs clean", r.returncode == 0, r.stderr.strip())
        hooks = repo / ".git" / "hooks"
        check("post-commit hook installed", (hooks / "post-commit").exists())

        # fake Claude Code session for this repo
        slug = project_slug(Path(_run(["git", "rev-parse", "--show-toplevel"], repo, env)
                                  .stdout.strip()))
        proj = home / ".claude" / "projects" / slug
        proj.mkdir(parents=True)
        session_file = proj / "sess-123.jsonl"
        session_file.write_text(session_lines("2026-08-13T10:00"))

        # --- commit #1 from a PLAIN TERMINAL (no Claude Code involved) -------
        (repo / "a.txt").write_text("hello")
        _run(["git", "add", "."], repo, env)
        r = _run(["git", "commit", "-q", "-m", "gui commit"], repo, env)
        check("plain-terminal commit succeeds", r.returncode == 0, r.stderr.strip())
        sha1 = _run(["git", "rev-parse", "HEAD"], repo, env).stdout.strip()

        note = _run(["git", "notes", f"--ref={NOTES_REF}", "show", sha1], repo, env)
        attached = note.returncode == 0
        bundle = json.loads(note.stdout) if attached else {}
        check("post-commit attached a note", attached, note.stderr.strip())
        check("bundle matches the commit", bundle.get("commit") == sha1)
        check("bundle has entries", len(bundle.get("entries", [])) >= 3)
        check("bundle meta counts prompts", bundle.get("meta", {}).get("prompts") == 1)
        check("bundle materialized locally", (proj / "commits" / f"{sha1}.json").exists())
        cursor = repo / ".git" / "claude-commit-context" / "sess-123.json"
        check("cursor advanced", cursor.exists()
              and json.loads(cursor.read_text()).get("last_sha") == sha1)

        # --- last_captured_ts parses the notes list --------------------------
        sha1_ts = int(_run(["git", "log", "-1", "--format=%ct", sha1], repo, env).stdout.strip())
        check("last_captured_ts == commit ts", last_captured_ts(repo) == sha1_ts)

        # --- PostToolUse capture path is idempotent with post-commit ---------
        payload = {
            "tool_input": {"command": "git commit -m 'x'"},
            "cwd": str(repo), "session_id": "sess-123", "transcript_path": str(session_file),
        }
        r = _run([sys.executable, "-m", "commit_context.cli", "capture"], repo, env,
                 input=json.dumps(payload))
        check("capture hook runs clean", r.returncode == 0, r.stderr.strip())
        n_notes = lambda: len(_run(["git", "notes", f"--ref={NOTES_REF}", "list"], repo, env)
                              .stdout.splitlines())
        check("capture did not double-attach", n_notes() == 1)

        # ...even when the cursor is missing, the note_exists guard holds
        cursor = repo / ".git" / "claude-commit-context" / "sess-123.json"
        cursor_text = cursor.read_text()
        cursor.unlink()
        _run([sys.executable, "-m", "commit_context.cli", "capture"], repo, env,
             input=json.dumps(payload))
        check("note_exists guard prevents re-attach", n_notes() == 1)
        cursor.write_text(cursor_text)  # restore so the empty-slice logic stays intact

        # --- commit #2: no new session activity -> no note -------------------
        (repo / "b.txt").write_text("world")
        _run(["git", "add", "."], repo, env)
        r = _run(["git", "commit", "-q", "-m", "gui commit 2"], repo, env)
        check("second plain commit succeeds", r.returncode == 0, r.stderr.strip())
        sha2 = _run(["git", "rev-parse", "HEAD"], repo, env).stdout.strip()
        note2 = _run(["git", "notes", f"--ref={NOTES_REF}", "show", sha2], repo, env)
        check("no note when nothing new happened", note2.returncode != 0, note2.stdout[:80])

        # --- stale-session guard ---------------------------------------------
        old = int(sha1_ts) - 1000
        os.utime(session_file, (old, old))
        (repo / "c.txt").write_text("stale")
        _run(["git", "add", "."], repo, env)
        _run(["git", "commit", "-q", "-m", "stale session"], repo, env)
        sha3 = _run(["git", "rev-parse", "HEAD"], repo, env).stdout.strip()
        note3 = _run(["git", "notes", f"--ref={NOTES_REF}", "show", sha3], repo, env)
        check("stale session skipped (no note)", note3.returncode != 0, note3.stdout[:80])

        # --- commit with NEW session activity -> attaches --------------------
        os.utime(session_file, None)  # restore mtime to now
        session_file.write_text(session_lines("2026-08-13T11:00"))
        (repo / "d.txt").write_text("fresh")
        _run(["git", "add", "."], repo, env)
        r = _run(["git", "commit", "-q", "-m", "fresh activity"], repo, env)
        check("commit with new activity succeeds", r.returncode == 0, r.stderr.strip())
        sha4 = _run(["git", "rev-parse", "HEAD"], repo, env).stdout.strip()
        note4 = _run(["git", "notes", f"--ref={NOTES_REF}", "show", sha4], repo, env)
        check("note attached for new activity", note4.returncode == 0, note4.stderr.strip())
        check("cursor advanced to sha4", json.loads(cursor.read_text()).get("last_sha") == sha4)

        # --- summary ----------------------------------------------------------
        print(f"\n{total_checks - len(failures)} passed, {len(failures)} failed")
        return 1 if failures else 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
