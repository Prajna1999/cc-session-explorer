"""Tests for the git-native ingest worker (M1).

Run from ``cloud/``::

    uv run python tests/worker_test.py

Covers: repo_identity normalization (ssh vs https), note-bundle ingestion from a
mirror clone, idempotent re-sync, the GitHub webhook endpoint with HMAC verification
(bad signature rejected), and the incremental push path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cc_cloud.config import get_settings  # noqa: E402
from cc_cloud.db import get_db  # noqa: E402
from cc_cloud.main import app  # noqa: E402
from cc_cloud.models import Base, Commit, CommitContext  # noqa: E402
from cc_cloud.worker import repo_identity, sync_project, sync_push, verify_github_signature  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  ok  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


def git(root: Path | None, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout


def make_repo_with_note(work: Path) -> tuple[str, dict]:
    """Create a repo with one commit carrying a claude-context note (capture.py shape)."""
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "alice@acme.dev")
    git(work, "config", "user.name", "Alice")
    (work / "f.txt").write_text("hello\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "initial commit")
    sha = git(work, "rev-parse", "HEAD").strip()

    bundle = {
        "schema_version": 1,
        "commit": sha,
        "session_id": "sess-1",
        "branch": "main",
        "captured_at": "2026-08-13T09:00:06Z",
        "entries": [
            {"kind": "user", "ts": "2026-08-13T09:00:01Z", "text": "write the worker", "meta": False},
            {"kind": "assistant", "ts": "2026-08-13T09:00:02Z", "text": "Done.", "model": "Sonnet 4"},
        ],
        "meta": {"models": ["Sonnet 4"], "branches": ["main"], "prompts": 1, "tool_calls": 1,
                 "tool_counts": {"Bash": 1}, "thinking": 0, "responses": 1},
    }
    tmp = work / "note.json"
    tmp.write_text(json.dumps(bundle))
    git(work, "notes", "--ref=refs/notes/claude-context", "add", "-F", str(tmp), sha)
    return sha, bundle


def build_client(repos_dir: Path, secret: str) -> TestClient:
    # CC_CLOUD_REPOS_DIR is read by get_settings() at import — re-point it so the
    # worker clones land in the test temp dir, and enable the webhook secret.
    settings = get_settings()
    settings.repos_dir = str(repos_dir)
    settings.webhook_secret = secret

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db = test_maker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), test_maker


def sign(payload: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def main() -> int:
    # --- repo_identity normalization ------------------------------------------
    check("ssh == https identity",
          repo_identity("git@github.com:a/b.git") == repo_identity("https://github.com/a/b.git"),
          str(repo_identity("git@github.com:a/b.git")))
    check("identity strips .git + case", repo_identity("https://github.com/A/B.git") == ("github.com", "A/B"))
    check("local path identity", repo_identity("/tmp/x/origin.git") == ("", "/tmp/x/origin"))

    # --- signature helper ------------------------------------------------------
    payload = b'{"hello":"world"}'
    check("signature verifies", verify_github_signature(payload, sign(payload, "sec"), "sec"))
    check("bad signature rejected", not verify_github_signature(payload, "sha256=deadbeef", "sec"))
    check("missing signature rejected", not verify_github_signature(payload, None, "sec"))

    import tempfile

    work = Path(tempfile.mkdtemp(prefix="cc-worker-"))
    try:
        # bare remote (what the worker clones), + a working clone for making commits
        remote = work / "origin.git"
        git(None, "init", "--bare", "-q", str(remote))
        clone = work / "clone"
        git(None, "clone", "-q", str(remote), str(clone))
        sha, bundle = make_repo_with_note(clone)
        # push branch AND the notes ref (this is what cc-commit-context's pre-push hook does)
        git(clone, "push", "-q", "origin", "main", f"refs/notes/claude-context:refs/notes/claude-context")

        c, maker = build_client(work / "repos", secret="webhook-secret")

        # --- backfill sync_project ----------------------------------------------
        with maker() as db:
            from cc_cloud.models import Project, Team, TeamMember, User

            user = User(email="alice@acme.dev", name="Alice", password_hash="x")
            db.add(user)
            db.flush()
            team = Team(slug="acme", name="Acme")
            db.add(team)
            db.flush()
            db.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))
            project = Project(team_id=team.id, slug="worker-demo", name="Worker Demo",
                              repo_url=str(remote))
            db.add(project)
            db.commit()
            project_id = project.id
            team_id = team.id

            summary = sync_project(db, project)
            check("sync_project ingests the commit", summary["commits_added"] == 1, str(summary))
            check("sync_project ingests the context", summary["contexts_added"] == 1, str(summary))

            commit = db.query(Commit).filter(Commit.project_id == project_id).one()
            check("commit metadata", commit.subject == "initial commit" and commit.author_email == "alice@acme.dev")
            check("commit has_context", commit.has_context is True)
            ctx = db.query(CommitContext).filter(CommitContext.commit_id == commit.id).one()
            check("context bundle stored", ctx.branch == "main" and len(ctx.entries or []) == 2)
            check("context linked session id", json.loads(ctx.raw)["session_id"] == "sess-1")

            again = sync_project(db, project)
            check("re-sync is a no-op", again["commits_added"] == 0 and again["commits_synced"] == 0, str(again))

        # --- webhook endpoint -----------------------------------------------------
        event = {
            "ref": "refs/heads/main",
            "before": "0" * 40,
            "after": sha,
            "repository": {"clone_url": str(remote), "html_url": str(remote)},
        }
        body = json.dumps(event).encode()
        r = c.post("/api/webhooks/github", content=body, headers={"X-Hub-Signature-256": sign(body, "webhook-secret")})
        check("webhook accepted", r.status_code == 200 and r.json()["accepted"] is True, f"{r.status_code} {r.text[:200]}")

        r = c.post("/api/webhooks/github", content=body, headers={"X-Hub-Signature-256": "sha256=bad"})
        check("webhook rejects bad signature", r.status_code == 401, str(r.status_code))

        r = c.post("/api/webhooks/github", content=body)  # no signature header
        check("webhook rejects missing signature", r.status_code == 401, str(r.status_code))

        # a second push with a new commit (incremental path)
        (clone / "f2.txt").write_text("second\n")
        git(clone, "add", ".")
        git(clone, "commit", "-q", "-m", "second commit")
        sha2 = git(clone, "rev-parse", "HEAD").strip()
        git(clone, "push", "-q", "origin", "main")
        event2 = {**event, "before": sha, "after": sha2}
        body2 = json.dumps(event2).encode()
        r = c.post("/api/webhooks/github", content=body2, headers={"X-Hub-Signature-256": sign(body2, "webhook-secret")})
        check("push webhook ingests new commit", r.status_code == 200 and r.json()["commits_added"] == 1, f"{r.status_code} {r.text[:200]}")
        check("new commit has no context", r.json()["contexts_added"] == 0, str(r.json()))

        # webhook for an unknown repo
        body3 = json.dumps({**event, "repository": {"clone_url": "/nonexistent/repo.git"}}).encode()
        r = c.post("/api/webhooks/github", content=body3, headers={"X-Hub-Signature-256": sign(body3, "webhook-secret")})
        check("webhook 404s for unknown repo", r.status_code == 404, str(r.status_code))

        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
        return 1 if FAILED else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
