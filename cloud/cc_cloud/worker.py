"""Git-native ingest worker (M1).

The commit context already travels with the repo on ``refs/notes/claude-context``
(see commit-context/), so the cloud needs **zero agent-side software** to show
teammates' context: the worker keeps a bare mirror clone of each project, fetches
new refs + the notes ref, reads each new commit's note bundle (if any) and commit
metadata, and upserts them through ``ingest.ingest_commits``.

Two entry points share the same core:

- ``POST /api/webhooks/github`` → ``sync_push`` (incremental, on every push)
- ``cc-cloud-worker backfill`` → ``sync_project`` (full resync, cron-able)
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .ingest import ingest_commits
from .models import Commit, Project
from .schemas import CommitIn, CommitsIngestIn

NOTES_REF = "refs/notes/claude-context"
ZERO_SHA = "0" * 40


class WorkerError(RuntimeError):
    """Git or ingest failure — the caller should surface it (webhook → 5xx)."""


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/]+@)?([^:/]+)(?::\d+)?[/:](.+?)(?:\.git)?/?$", re.I)


def repo_identity(url: str) -> tuple[str, str]:
    """Normalize any common git URL form to ``(host, path)`` for matching.

    ``git@github.com:a/b.git`` and ``https://github.com/a/b.git`` both become
    ``("github.com", "a/b")`` — this is what lets a GitHub webhook (https clone
    URL) find a project registered with an SSH remote. Local paths (tests, file://)
    get host ``""`` and are matched by path.
    """
    url = (url or "").strip()
    if url.startswith("file://"):
        return ("", url.removeprefix("file://").removesuffix(".git").rstrip("/"))
    m = _URL_RE.match(url)
    if m:
        return (m.group(1).lower(), m.group(2).rstrip("/"))
    return ("", url.removesuffix(".git").rstrip("/"))


# ---------------------------------------------------------------------------
# Git plumbing
# ---------------------------------------------------------------------------

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(slug: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(slug, threading.Lock())


def run_git(root: Path | None, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise WorkerError(f"git {args[0] if args else ''} failed: {e}") from e


def project_repo_dir(project: Project) -> Path:
    return Path(get_settings().repos_dir) / project.slug


def ensure_repo(project: Project) -> Path:
    """Mirror-clone the project's repo on first use, then fetch updates."""
    d = project_repo_dir(project)
    if not (d / "HEAD").exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        r = run_git(None, "clone", "--mirror", project.repo_url, str(d))
        if r.returncode != 0:
            raise WorkerError(f"clone failed for {project.repo_url}: {r.stderr.strip()}")
        return d
    # mirror clones have remote.origin.mirror=true → `fetch` covers refs/notes too.
    r = run_git(d, "fetch", "--prune", "origin")
    if r.returncode != 0:
        raise WorkerError(f"fetch failed for {project.slug}: {r.stderr.strip()}")
    return d


def all_commit_shas(root: Path) -> list[str]:
    # --branches --tags, deliberately NOT --all: --all would also traverse
    # refs/notes/claude-context (a commit object), which is not a repo commit.
    r = run_git(root, "rev-list", "--branches", "--tags")
    if r.returncode != 0:
        raise WorkerError(f"rev-list failed: {r.stderr.strip()}")
    return [s for s in r.stdout.splitlines() if s.strip()]


def commit_metadata(root: Path, sha: str) -> dict:
    r = run_git(root, "show", "-s", "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%s", sha)
    if r.returncode != 0 or not r.stdout.strip():
        raise WorkerError(f"cannot read commit {sha[:7]}")
    parts = r.stdout.strip().split("\x1f")
    if len(parts) != 5:
        raise WorkerError(f"unexpected metadata for {sha[:7]}")
    refs = [
        line.split()[0]
        for line in run_git(root, "for-each-ref", "--format=%(refname:short)", f"--points-at={sha}").stdout.splitlines()
    ]
    parents = run_git(root, "rev-list", "--parents", "-n", "1", sha).stdout.strip().split()[1:]
    return {
        "sha": parts[0],
        "author_name": parts[1],
        "author_email": parts[2],
        "authored_at": parts[3],
        "subject": parts[4],
        "refs": refs,
        "parents": parents,
    }


def note_bundle(root: Path, sha: str) -> dict | None:
    """The agent-context note for a commit, or None when it has none."""
    r = run_git(root, "notes", f"--ref={NOTES_REF}", "show", sha)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def commit_payload(root: Path, sha: str) -> CommitIn:
    """CommitIn with git metadata + optional context from the notes ref."""
    meta = commit_metadata(root, sha)
    bundle = note_bundle(root, sha)
    context = None
    if bundle:
        context = {
            "schema_version": bundle.get("schema_version", 1),
            "session_id": bundle.get("session_id"),
            "branch": bundle.get("branch"),
            "captured_at": bundle.get("captured_at"),
            "entries": [{"seq": i, **e} for i, e in enumerate(bundle.get("entries", []))],
            "meta": bundle.get("meta"),
        }
    return CommitIn(**meta, context=context)


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------


def ingest_shas(db: Session, project: Project, root: Path, shas: list[str], user=None) -> dict:
    if not shas:
        return {"commits_added": 0, "contexts_added": 0, "commits_synced": 0}
    commits = [commit_payload(root, s) for s in shas]
    result = ingest_commits(db, project, user, CommitsIngestIn(project=project.slug, commits=commits))
    return {
        "commits_added": result.accepted,
        "contexts_added": result.contexts_added,
        "commits_synced": len(shas),
    }


def sync_project(db: Session, project: Project, user=None, limit: int = 2000) -> dict:
    """Fetch and ingest all commits not already in the DB (backfill / cron)."""
    with _lock_for(project.slug):
        root = ensure_repo(project)
        known = {row[0] for row in db.query(Commit.sha).filter(Commit.project_id == project.id)}
        new_shas = [s for s in all_commit_shas(root) if s not in known][:limit]
        return ingest_shas(db, project, root, new_shas, user)


def sync_push(db: Session, project: Project, before: str, after: str, ref: str, user=None) -> dict:
    """Fetch and ingest the commits a push introduced (webhook path)."""
    with _lock_for(project.slug):
        root = ensure_repo(project)
        if not after or all(c == "0" for c in after):
            return {"commits_added": 0, "contexts_added": 0, "commits_synced": 0}  # branch deleted
        if before and not all(c == "0" for c in before):
            r = run_git(root, "rev-list", f"{before}..{after}")
        else:  # new branch/tag: everything reachable from `after`
            r = run_git(root, "rev-list", after)
        if r.returncode != 0:
            raise WorkerError(f"rev-list failed: {r.stderr.strip()}")
        shas = [s for s in r.stdout.splitlines() if s.strip()]
        return ingest_shas(db, project, root, shas, user)


# ---------------------------------------------------------------------------
# Webhook signature (GitHub style: X-Hub-Signature-256)
# ---------------------------------------------------------------------------


def verify_github_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# CLI (cron backfill)
# ---------------------------------------------------------------------------


def cmd_backfill(args: argparse.Namespace) -> int:
    # Match the API's dev behavior: self-create tables on SQLite (prod runs Alembic).
    from .db import engine, init_db

    if engine.dialect.name == "sqlite":
        init_db()
    db = SessionLocal()
    try:
        query = db.query(Project)
        if args.slug:
            query = query.filter(Project.slug == args.slug)
        for project in query.all():
            try:
                summary = sync_project(db, project)
                print(f"{project.slug}: {summary}")
            except WorkerError as e:
                print(f"{project.slug}: ERROR {e}", file=sys.stderr)
                db.rollback()
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cc-cloud-worker", description="CC Explorer Cloud git-ingest worker")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("backfill", help="sync all projects' git histories (cron me)")
    p.add_argument("--slug", default=None, help="only sync this project slug")
    p.set_defaults(func=cmd_backfill)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
