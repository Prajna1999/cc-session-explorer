import subprocess
import sys
from pathlib import Path

from .materialize import repo_root, shas_for_all, materialize

INSTALL_MARKER = "# Installed by commit-context (cc-commit-context install) — do not edit by hand."

POST_MERGE = f"""#!/bin/sh
{INSTALL_MARKER}
cc-commit-context materialize --range "${{ORIG_HEAD:-}}" HEAD
"""

POST_CHECKOUT = f"""#!/bin/sh
{INSTALL_MARKER}
cc-commit-context materialize --range "$1" "$2"
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

    _run(["git", "config", "--add", "remote.origin.fetch",
          "+refs/notes/claude-context:refs/notes/claude-context"], cwd=root)
    _run(["git", "config", "--add", "remote.origin.push",
          "refs/notes/claude-context:refs/notes/claude-context"], cwd=root)
    messages.append("configured remote.origin fetch/push refspecs for refs/notes/claude-context")

    if _run(["git", "remote", "get-url", "origin"], cwd=root).returncode != 0:
        messages.append("note: no 'origin' remote configured yet — refspecs are set but inactive until one exists")

    hooks_dir = root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    messages.append(_write_hook(hooks_dir, "post-merge", POST_MERGE))
    messages.append(_write_hook(hooks_dir, "post-checkout", POST_CHECKOUT))

    fetch = _run(["git", "fetch", "origin",
                  "refs/notes/claude-context:refs/notes/claude-context"], cwd=root)
    if fetch.returncode == 0:
        messages.append("fetched existing refs/notes/claude-context from origin")

    written = materialize(shas_for_all(root), root)
    messages.append(f"materialized context for {written} commit(s) already reachable")

    for m in messages:
        print(m)
    return 0
