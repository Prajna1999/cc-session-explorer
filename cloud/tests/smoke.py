"""End-to-end smoke test for the cloud API.

Run from ``cloud/``::

    uv run python tests/smoke.py

Covers: register/login, team auto-creation, project creation, session ingest
(entries + subagents + tool results), commit-context ingest, all read endpoints
(local-API-compatible shapes), idempotent re-ingest, and authorization (401/403).
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cc_cloud.db import create_engine_and_sessionmaker, get_db  # noqa: E402
from cc_cloud.main import app  # noqa: E402
from cc_cloud.models import Base  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  ok  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


def build_client() -> TestClient:
    engine, maker = create_engine_and_sessionmaker(
        "sqlite://",  # in-memory; the factory's FK pragma listener applies
    )
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
    return TestClient(app)


def main() -> int:
    c = build_client()

    # --- auth: no credentials -------------------------------------------------
    r = c.get("/api/projects")
    check("401 without token", r.status_code == 401, str(r.status_code))

    # --- register + login ------------------------------------------------------
    r = c.post("/api/auth/register", json={"email": "alice@acme.dev", "name": "Alice", "password": "hunter2hunter2"})
    check("register 200", r.status_code == 200, str(r.status_code))
    alice = r.json()
    alice_headers = {"Authorization": f"Bearer {alice['token']}"}
    check("register returns token", bool(alice["token"]))

    r = c.post("/api/auth/login", json={"email": "alice@acme.dev", "password": "hunter2hunter2"})
    check("login 200", r.status_code == 200, str(r.status_code))
    check("login token works", r.json()["token"] == alice["token"])

    r = c.post("/api/auth/login", json={"email": "alice@acme.dev", "password": "wrongpass"})
    check("login rejects bad password", r.status_code == 401, str(r.status_code))

    r = c.get("/api/me", headers=alice_headers)
    me = r.json()
    team = me["teams"][0]
    check("me returns personal team", team["role"] == "owner", str(me))
    team_headers = {**alice_headers}

    # --- API token ------------------------------------------------------------
    r = c.post("/api/auth/tokens", json={"name": "cli"}, headers=alice_headers)
    check("create api token", r.status_code == 200 and r.json()["token"].startswith("cc_"), str(r.status_code))
    cli_headers = {"Authorization": f"Bearer {r.json()['token']}"}
    check("api token authenticates", c.get("/api/me", headers=cli_headers).status_code == 200)

    # --- project ---------------------------------------------------------------
    r = c.post(
        "/api/projects",
        json={
            "team_id": team["id"],
            "name": "CC Session Explorer",
            "repo_url": "git@github.com:acme/cc-session-explorer.git",
            "slug": "cc-session-explorer",
        },
        headers=team_headers,
    )
    check("create project", r.status_code == 201, str(r.status_code))
    check("project label", r.json()["label"] == "CC Session Explorer")

    r = c.get("/api/projects", headers=team_headers)
    check("list projects", r.status_code == 200 and len(r.json()) == 1)
    check("project has local-API shape", set(r.json()[0]) >= {"name", "cwd", "label", "count", "last"})

    # --- ingest a session --------------------------------------------------------
    sha = "9" * 40
    session_id = "2a7b6f5e-1111-4f1a-9a2b-000000000001"
    session_payload = {
        "project": "cc-session-explorer",
        "device": {"hostname": "mbp-alice", "os": "darwin", "claude_version": "2.0.0"},
        "sessions": [
            {
                "external_id": session_id,
                "title": "Build the SQL models",
                "cwd": "/Users/alice/cc-session-explorer",
                "branch": "main",
                "version": "2.0.0",
                "started_at": "2026-08-13T10:00:00+00:00",
                "ended_at": "2026-08-13T11:30:00+00:00",
                "models": ["claude-sonnet-4-20250514"],
                "branches": ["main"],
                "prompts": 2,
                "tool_calls": 2,
                "tool_counts": {"Read": 2},
                "thinking": 1,
                "responses": 1,
                "size_bytes": 4096,
                "has_subagents": True,
                "has_tool_results": True,
                "entries": [
                    {"seq": 0, "kind": "branch", "ts": "2026-08-13T10:00:00+00:00", "text": "main"},
                    {"seq": 1, "kind": "user", "ts": "2026-08-13T10:00:01+00:00", "text": "Build the SQLModels", "meta": False},
                    {"seq": 2, "kind": "assistant", "ts": "2026-08-13T10:00:02+00:00", "text": "Let me design the schema.", "model": "Sonnet 4"},
                    {"seq": 3, "kind": "thinking", "ts": "2026-08-13T10:00:02+00:00", "text": "users -> teams -> projects..."},
                    {"seq": 4, "kind": "tool", "ts": "2026-08-13T10:00:03+00:00", "name": "Read",
                     "arg": "models.py", "params": [{"key": "file_path", "value": "models.py"}],
                     "result": "class User(Base): ...", "persisted": "models.py"},
                    {"seq": 5, "kind": "system", "ts": "2026-08-13T10:00:04+00:00", "text": "turn · 5s · 3 messages"},
                ],
                "subagents": [
                    {"external_id": "agent-abc123", "agent_type": "Task", "description": "design review", "size_bytes": 1024,
                     "entries": [{"seq": 0, "kind": "user", "ts": "2026-08-13T10:10:00+00:00", "text": "review the schema", "meta": False}]},
                ],
                "tool_results": [{"name": "models.py", "size_bytes": 42, "content": "class User(Base):\n    pass\n"}],
            }
        ],
    }
    r = c.post("/api/ingest/sessions", json=session_payload, headers=cli_headers)
    check("ingest sessions 200", r.status_code == 200, str(r.status_code))
    res = r.json()
    check("ingest counts", res["accepted"] == 1 and res["entries_added"] == 6 and res["subagents_added"] == 1, str(res))

    # idempotent re-ingest
    r = c.post("/api/ingest/sessions", json=session_payload, headers=cli_headers)
    res2 = r.json()
    check("re-ingest is an update not a duplicate", res2["accepted"] == 0 and res2["updated"] == 1, str(res2))

    # --- read: project view -----------------------------------------------------
    r = c.get("/api/projects/cc-session-explorer", headers=team_headers)
    pv = r.json()
    check("project view 200", r.status_code == 200 and pv["total"] == 1, str(r.status_code))
    group = pv["groups"][0]["sessions"][0]
    check("session summary shape", group["id"] == session_id and group["branch"] == "main" and group["model"], str(group))
    check("session summary author", group["author"] and group["author"]["email"] == "alice@acme.dev", str(group))

    # --- read: session -----------------------------------------------------------
    r = c.get(f"/api/projects/cc-session-explorer/sessions/{session_id}", headers=team_headers)
    sv = r.json()
    kinds = [e["kind"] for e in sv["entries"]]
    check("session entries", kinds == ["branch", "user", "assistant", "thinking", "tool", "system"], str(kinds))
    tool = [e for e in sv["entries"] if e["kind"] == "tool"][0]
    check("tool entry fields", tool["name"] == "Read" and tool["params"] and tool["result"], str(tool))
    check("session meta", sv["meta"]["prompts"] == 2 and sv["meta"]["tool_counts"] == {"Read": 2})
    check("has flags", sv["has_subagents"] and sv["has_tool_results"])
    check("author on session", sv["author"]["email"] == "alice@acme.dev")

    # --- read: subagents + tool results -------------------------------------------
    r = c.get(f"/api/projects/cc-session-explorer/sessions/{session_id}/subagents", headers=team_headers)
    agents = r.json()
    check("subagents list", len(agents) == 1 and agents[0]["type"] == "Task", str(agents))
    r = c.get(f"/api/projects/cc-session-explorer/sessions/{session_id}/subagents/{agents[0]['id']}", headers=team_headers)
    check("subagent transcript", r.status_code == 200 and r.json()["entries"][0]["kind"] == "user")
    r = c.get(f"/api/projects/cc-session-explorer/sessions/{session_id}/tool-results", headers=team_headers)
    check("tool results list", r.status_code == 200 and r.json()[0]["name"] == "models.py")
    r = c.get(f"/api/projects/cc-session-explorer/sessions/{session_id}/tool-results/models.py", headers=team_headers)
    check("tool result file", r.status_code == 200 and "class User" in r.text)

    # --- ingest commits + context --------------------------------------------------
    commit_payload = {
        "project": "cc-session-explorer",
        "commits": [
            {
                "sha": sha,
                "subject": "Add SQL models for the cloud backend",
                "author_name": "Alice",
                "author_email": "alice@acme.dev",
                "authored_at": "2026-08-13T11:31:00+00:00",
                "refs": ["HEAD -> main", "origin/main"],
                "parents": ["1" * 40],
                "context": {
                    "schema_version": 1,
                    "session_id": session_id,  # must link to the ingested session
                    "branch": "main",
                    "captured_at": "2026-08-13T11:31:05+00:00",
                    "entries": [
                        {"seq": 0, "kind": "user", "ts": "2026-08-13T11:20:00+00:00", "text": "commit the models", "meta": False},
                        {"seq": 1, "kind": "assistant", "ts": "2026-08-13T11:21:00+00:00", "text": "git commit -m 'Add SQL models'", "model": "Sonnet 4"},
                        {"seq": 2, "kind": "tool", "ts": "2026-08-13T11:21:01+00:00", "name": "Bash",
                         "arg": "git commit", "params": [{"key": "command", "value": "git commit -m ..."}], "result": "[main abc1234] ..."},
                    ],
                    "meta": {"models": ["Sonnet 4"], "branches": ["main"], "prompts": 1, "tool_calls": 1,
                             "tool_counts": {"Bash": 1}, "thinking": 0, "responses": 1},
                },
            }
        ],
    }
    r = c.post("/api/ingest/commits", json=commit_payload, headers=cli_headers)
    check("ingest commits 200", r.status_code == 200, str(r.status_code))
    check("commit ingest counts", r.json()["accepted"] == 1 and r.json()["contexts_added"] == 1, str(r.json()))

    # --- read: git + commit context ------------------------------------------------
    r = c.get("/api/projects/cc-session-explorer/git", headers=team_headers)
    gv = r.json()
    check("git history", len(gv["commits"]) == 1 and gv["commits"][0]["sha"] == sha, str(gv))
    check("git has_context", gv["commits"][0]["has_context"] is True)
    check("git author", gv["commits"][0]["author"] == "Alice")

    r = c.get(f"/api/projects/cc-session-explorer/commits/{sha}", headers=team_headers)
    cc = r.json()
    check("commit context linked session", cc["session_id"] == session_id, str(cc))
    check("commit context entries", len(cc["entries"]) == 3 and cc["meta"]["prompts"] == 1)

    # --- authorization ---------------------------------------------------------------
    r = c.post("/api/auth/register", json={"email": "bob@acme.dev", "name": "Bob", "password": "hunter2hunter2"})
    bob_headers = {"Authorization": f"Bearer {r.json()['token']}"}
    check("bob sees no projects", c.get("/api/projects", headers=bob_headers).json() == [])
    check("bob blocked from project", c.get("/api/projects/cc-session-explorer", headers=bob_headers).status_code == 403)
    check("bob blocked from session", c.get(f"/api/projects/cc-session-explorer/sessions/{session_id}", headers=bob_headers).status_code == 403)
    check("bob blocked from git", c.get("/api/projects/cc-session-explorer/git", headers=bob_headers).status_code == 403)

    # --- cursors ----------------------------------------------------------------------
    r = c.get("/api/ingest/cursor", params={"project": "cc-session-explorer", "kind": "sessions", "device": "mbp-alice"}, headers=cli_headers)
    check("cursor get empty", r.status_code == 200 and r.json()["last_marker"] is None, str(r.json()))
    r = c.post("/api/ingest/cursor", json={"project": "cc-session-explorer", "kind": "sessions", "device": "mbp-alice", "last_marker": "2026-08-13T12:00:00"}, headers=cli_headers)
    check("cursor set", r.status_code == 200 and r.json()["last_marker"] == "2026-08-13T12:00:00", str(r.json()))

    # --- members -----------------------------------------------------------------------
    r = c.get("/api/projects/cc-session-explorer/members", headers=team_headers)
    check("members", r.status_code == 200 and any(m["email"] == "alice@acme.dev" for m in r.json()["members"]), str(r.json()))

    # --- health ------------------------------------------------------------------------
    check("healthz", c.get("/healthz").status_code == 200)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
