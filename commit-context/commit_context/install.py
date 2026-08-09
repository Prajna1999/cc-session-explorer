import subprocess
import sys
from pathlib import Path

from .materialize import repo_root, shas_for_all, materialize

INSTALL_MARKER = "# Installed by commit-context (cc-commit-context install) — do not edit by hand."
NOTES_REFSPEC = "refs/notes/claude-context:refs/notes/claude-context"

# Deliberately NOT using `remote.origin.fetch`/`remote.origin.push` config for the
# notes ref: git treats configured refspecs as one atomic transaction, so if the
# notes ref doesn't exist yet on the remote (true for any repo before its first
# captured commit is pushed), a bare `git fetch`/`git push` fails OUTRIGHT — not
# just for the missing notes ref, but for the branch too. Instead, each hook below
# does its own best-effort, error-swallowed fetch/push of the notes ref, entirely
# separate from the real fetch/push, so a missing notes ref never blocks anything.

POST_MERGE = f"""#!/bin/sh
{INSTALL_MARKER}
git fetch origin '{NOTES_REFSPEC}' >/dev/null 2>&1 || true
cc-commit-context materialize --range "${{ORIG_HEAD:-}}" HEAD
"""

POST_CHECKOUT = f"""#!/bin/sh
{INSTALL_MARKER}
git fetch origin '{NOTES_REFSPEC}' >/dev/null 2>&1 || true
cc-commit-context materialize --range "$1" "$2"
"""

# Guarded against recursion: this hook's own `git push` of the notes ref would
# otherwise re-trigger this same pre-push hook.
PRE_PUSH = f"""#!/bin/sh
{INSTALL_MARKER}
if [ -n "$CC_COMMIT_CONTEXT_PRE_PUSH" ]; then
  exit 0
fi
CC_COMMIT_CONTEXT_PRE_PUSH=1 git push "$1" '{NOTES_REFSPEC}' >/dev/null 2>&1 || true
exit 0
"""


def _run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _write_hook(hooks_dir: Path, name: str, content: str) -> str:
    path = hooks_dir / name
    if path.exists():
        existing = path.read_text(errors="replace")
        if INSTALL_MARKER not in existing:
            return f"skipped {name}: an existing hook is already installed there (not overwriting)"
    path.write_text(content)
    path.chmod(0o755)
    return f"installed {name}"


def run(argv=None) -> int:
    try:
        root = repo_root()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    messages = []

    if _run(["git", "remote", "get-url", "origin"], cwd=root).returncode != 0:
        messages.append("note: no 'origin' remote configured yet — notes sync hooks are installed but inactive until one exists")

    hooks_dir = root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    messages.append(_write_hook(hooks_dir, "post-merge", POST_MERGE))
    messages.append(_write_hook(hooks_dir, "post-checkout", POST_CHECKOUT))
    messages.append(_write_hook(hooks_dir, "pre-push", PRE_PUSH))

    fetch = _run(["git", "fetch", "origin", NOTES_REFSPEC], cwd=root)
    if fetch.returncode == 0:
        messages.append("fetched existing refs/notes/claude-context from origin")

    written = materialize(shas_for_all(root), root)
    messages.append(f"materialized context for {written} commit(s) already reachable")

    for m in messages:
        print(m)
    return 0
