import argparse
import subprocess
from pathlib import Path

NOTES_REF = "refs/notes/claude-context"
NULL_SHA = "0" * 40


def _run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def repo_root(cwd=None) -> Path:
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError("not inside a git repository")
    return Path(result.stdout.strip())


def project_slug(root: Path) -> str:
    return str(root).replace("/", "-")


def commits_dir(root: Path) -> Path:
    return Path.home() / ".claude" / "projects" / project_slug(root) / "commits"


def shas_for_all(root: Path) -> list[str]:
    out = _run(["git", "rev-list", "--all"], cwd=root)
    return [s for s in out.stdout.splitlines() if s.strip()]


def shas_for_range(root: Path, old: str | None, new: str) -> list[str]:
    if not old or old == NULL_SHA:
        out = _run(["git", "rev-list", new], cwd=root)
    else:
        out = _run(["git", "rev-list", f"{old}..{new}"], cwd=root)
    return [s for s in out.stdout.splitlines() if s.strip()]


def materialize(shas: list[str], root: Path) -> int:
    if not shas:
        return 0
    out_dir = commits_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for sha in shas:
        note = _run(["git", "notes", f"--ref={NOTES_REF}", "show", sha], cwd=root)
        if note.returncode != 0:
            continue  # no context for this commit — expected, not an error
        (out_dir / f"{sha}.json").write_text(note.stdout)
        written += 1
    return written


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cc-commit-context materialize")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--range", nargs=2, metavar=("OLD", "NEW"))
    args = parser.parse_args(argv)

    root = repo_root()
    if args.all:
        shas = shas_for_all(root)
    elif args.range:
        shas = shas_for_range(root, args.range[0], args.range[1])
    else:
        parser.error("one of --all or --range OLD NEW is required")
        return 2

    materialize(shas, root)
    return 0
