"""Native ``post-commit`` capture: context for commits made outside Claude Code.

The Claude Code ``PostToolUse`` capture hook only fires when ``git commit`` runs
as a Bash tool call *inside* a Claude Code session. Commits made from a GUI (VS
Code, GitHub Desktop), a plain terminal, or another tool never trigger it — and
until this hook existed, those commits got no attached context.

This hook runs for **every** commit (``git commit`` also fires it for amend,
cherry-pick, etc.). It attaches the most recently active Claude Code session's
context for the repo — the conversation that led up to the commit — to any
commit that doesn't already have a note, so the user never has to remember to
commit from inside Claude Code to get context captured.

Heuristics (deliberately conservative — no context is better than wrong context):

- a commit that already has a note is never touched (that's the in-session
  capture, which runs a moment later via PostToolUse and is skipped here);
- the candidate session is the newest ``*.jsonl`` under
  ``~/.claude/projects/<slug>/`` for this repo;
- sessions whose last activity is older than ``STALE_WINDOW`` (48h) are ignored
  — a conversation from days ago is not the context behind today's commit;
- sessions whose last activity predates the most recent already-captured commit
  are also ignored (already covered by later context);
- if the slice since the last captured commit is empty, nothing is attached.
"""

from pathlib import Path
from time import time

from .capture import build_and_attach, cursor_path, head_sha, last_captured_ts, note_exists
from .materialize import project_slug, repo_root

STALE_WINDOW = 48 * 3600  # seconds; older sessions are not "the context behind" today's commit


def run(argv=None) -> int:
    try:
        root = repo_root()
    except RuntimeError:
        return 0  # not a git repo — nothing to do

    sha = head_sha(root)
    if sha is None or note_exists(root, sha):
        return 0

    proj_dir = Path.home() / ".claude" / "projects" / project_slug(root)
    if not proj_dir.is_dir():
        return 0  # no Claude Code sessions for this repo — nothing to attach

    sessions = sorted(proj_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not sessions:
        return 0
    transcript = sessions[0]
    session_mtime = int(transcript.stat().st_mtime)

    if time() - session_mtime > STALE_WINDOW:
        return 0  # session ended too long ago to be the context behind this commit

    last_ts = last_captured_ts(root)
    if last_ts is not None and session_mtime < last_ts:
        return 0  # newest session predates the last captured context — stale

    session_id = transcript.stem
    build_and_attach(root, sha, session_id, transcript, cursor_path(root, session_id))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(run())
