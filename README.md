# CC Session Explorer

Explore Claude Code session history — a Linear-style UI over `~/.claude/projects`,
plus a git-notes system that attaches the agent conversation behind each commit so it
travels with the repo.

## Repo layout

| Path | What it is |
|---|---|
| `main.py` | Local FastAPI JSON API over `~/.claude/projects` (per-machine) |
| `frontend/` | Next.js (App Router) + shadcn UI, server-side fetches via `API_BASE_URL` |
| `commit-context/` | Dependency-free package: attach agent context to commits via `refs/notes/claude-context` (`cc-commit-context` CLI) |
| `cloud/` | **Hosted, multi-user version** — SQLAlchemy models, Alembic migrations, cloud FastAPI API, `cc-cloud` sync CLI (see `docs/system-design.md`) |
| `docs/system-design.md` | System design for the cloud, multi-user product |

## Local dev

```sh
uv run fastapi dev main.py     # API on :8000 (serves ~/.claude/projects)
cd frontend && npm run dev     # UI on :3000
```

Commit context setup (once per machine/clone):

```sh
uv tool install --from ./commit-context commit-context
cc-commit-context install
```

## Cloud (multi-user, shared team context)

The cloud backend ingests session logs and per-commit agent context from every team
member's machine and serves it through the same frontend, now team-aware. Two paths:

1. **Git-native (zero install):** commit context rides git notes; a GitHub webhook
   (`POST /api/webhooks/github`) + the `cc-cloud-worker backfill` cron ingest it with
   no agent-side software (M1 — done).
2. **Opt-in full logs:** `cc-cloud sync --project <repo> --sessions --commits` uploads
   full transcripts from a dev machine (M2 — done).

```sh
cd cloud && uv sync
uv run uvicorn cc_cloud.main:app --reload --port 8000   # SQLite dev DB, auto-created
uv run alembic upgrade head                             # prod: Postgres via CC_CLOUD_DATABASE_URL
uv run python tests/smoke.py                            # in-process API checks
uv run python tests/e2e_cli.py                          # live server + real CLI sync
uv run python tests/worker_test.py                      # git webhook ingest checks
```

See `cloud/README.md` for the CLI and `docs/system-design.md` for the full design.
