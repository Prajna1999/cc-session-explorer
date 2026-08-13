"""Full end-to-end test: live uvicorn server + the real ``cc-cloud`` sync CLI.

Run from ``backend/``::

    uv run python tests/e2e_cli.py

Sets up a scratch git repo with a commit, a fake Claude Code session jsonl +
commit-context bundle in a fake ``~/.claude/projects`` tree, then runs the CLI
(``python -m cc_cloud sync --sessions --commits``) against a live server and
verifies the data is browsable through the read API.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
WORK = Path("/tmp/cc-cloud-e2e")
REPO = (WORK / "repo").resolve()  # resolve: macOS /tmp -> /private/tmp, and the CLI resolves too
FAKE_HOME = (WORK / "home").resolve()
DB = WORK / "cloud.db"

ORIGIN_URL = "git@example.com:acme/demo.git"
SERVER_SLUG = "demo"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_healthy(base: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError("server did not become healthy in time")


def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    # --- scratch git repo with one commit ------------------------------------
    REPO.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=REPO, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "alice@acme.dev"], cwd=REPO, check=True)
    subprocess.run(["git", "config", "user.name", "Alice"], cwd=REPO, check=True)
    (REPO / "README.md").write_text("# demo\n")
    subprocess.run(["git", "add", "."], cwd=REPO, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=REPO, check=True)
    subprocess.run(["git", "remote", "add", "origin", ORIGIN_URL], cwd=REPO, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()

    # --- fake ~/.claude/projects tree ------------------------------------------
    slug = str(REPO).replace("/", "-")  # mirrors commit_context.materialize.project_slug
    proj_dir = FAKE_HOME / ".claude" / "projects" / slug
    (proj_dir / "commits").mkdir(parents=True)

    session_id = str(uuid.uuid4())
    ts = "2026-08-13T09:00:00Z"
    jsonl_lines = [
        {"type": "ai-title", "aiTitle": "Demo session", "timestamp": ts},
        {"type": "user", "message": {"content": "build something"}, "timestamp": "2026-08-13T09:00:01Z",
         "cwd": str(REPO), "gitBranch": "main", "version": "2.0.0"},
        {"type": "assistant", "message": {"model": "claude-sonnet-4-20250514",
                                          "content": [{"type": "text", "text": "On it."}]},
         "timestamp": "2026-08-13T09:00:02Z"},
        {"type": "assistant", "message": {"model": "claude-sonnet-4-20250514",
                                          "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                                      "input": {"command": "git commit -m demo"}}]},
         "timestamp": "2026-08-13T09:00:03Z"},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                  "content": [{"type": "text", "text": f"[main {sha[:7]}] demo"}]}]},
         "timestamp": "2026-08-13T09:00:04Z"},
        {"type": "system", "subtype": "turn_duration", "durationMs": 1000, "messageCount": 2,
         "timestamp": "2026-08-13T09:00:05Z"},
    ]
    (proj_dir / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(o) for o in jsonl_lines) + "\n"
    )

    bundle = {
        "schema_version": 1,
        "commit": sha,
        "session_id": session_id,
        "branch": "main",
        "captured_at": "2026-08-13T09:00:06Z",
        "entries": [
            {"kind": "user", "ts": "2026-08-13T09:00:01Z", "text": "build something", "meta": False},
            {"kind": "assistant", "ts": "2026-08-13T09:00:02Z", "text": "On it.", "model": "Sonnet 4"},
        ],
        "meta": {"models": ["Sonnet 4"], "branches": ["main"], "prompts": 1, "tool_calls": 1,
                 "tool_counts": {"Bash": 1}, "thinking": 0, "responses": 1},
    }
    (proj_dir / "commits" / f"{sha}.json").write_text(json.dumps(bundle))

    # --- start server ------------------------------------------------------------
    port = free_port()
    env = {**os.environ, "CC_CLOUD_DATABASE_URL": f"sqlite:///{DB}"}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "cc_cloud.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_healthy(base)
        with httpx.Client(base_url=base, timeout=30) as c:
            r = c.post("/api/auth/register", json={"email": "alice@acme.dev", "name": "Alice", "password": "hunter2hunter2"})
            r.raise_for_status()
            jwt = r.json()["token"]
            h = {"Authorization": f"Bearer {jwt}"}
            team_id = c.get("/api/me", headers=h).json()["teams"][0]["id"]
            r = c.post("/api/projects", json={"team_id": team_id, "name": "Demo", "repo_url": ORIGIN_URL, "slug": SERVER_SLUG}, headers=h)
            r.raise_for_status()
            r = c.post("/api/auth/tokens", json={"name": "cli"}, headers=h)
            r.raise_for_status()
            token = r.json()["token"]

        # --- run the CLI -----------------------------------------------------------
        cli_env = {**os.environ, "HOME": str(FAKE_HOME), "CC_CLOUD_API_URL": base, "CC_CLOUD_TOKEN": token}
        run = subprocess.run(
            [sys.executable, "-m", "cc_cloud", "sync", "--project", str(REPO), "--sessions", "--commits"],
            cwd=ROOT, env=cli_env, capture_output=True, text=True, timeout=120,
        )
        print(run.stdout)
        if run.returncode != 0:
            print(run.stderr, file=sys.stderr)
            raise SystemExit("CLI sync failed")

        # --- verify through the read API ---------------------------------------------
        with httpx.Client(base_url=base, timeout=30) as c:
            h = {"Authorization": f"Bearer {jwt}"}
            pv = c.get(f"/api/projects/{SERVER_SLUG}", headers=h).json()
            assert pv["total"] == 1, pv
            sv = c.get(f"/api/projects/{SERVER_SLUG}/sessions/{session_id}", headers=h).json()
            kinds = [e["kind"] for e in sv["entries"]]
            assert kinds == ["branch", "user", "assistant", "tool", "system"], kinds
            assert sv["author"]["email"] == "alice@acme.dev", sv["author"]
            gv = c.get(f"/api/projects/{SERVER_SLUG}/git", headers=h).json()
            assert gv["commits"] and gv["commits"][0]["sha"] == sha and gv["commits"][0]["has_context"], gv
            cc = c.get(f"/api/projects/{SERVER_SLUG}/commits/{sha}", headers=h).json()
            assert cc["session_id"] == session_id, cc
            assert len(cc["entries"]) == 2, cc

        print("E2E OK: CLI uploaded the session + commit context; read API returns them with the right shapes.")
        return 0
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
