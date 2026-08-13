# cc-cloud — CC Explorer Cloud backend + sync CLI

Hosted, multi-user backend for the CC session explorer: teams share the Claude Code
session logs and per-commit agent context of everyone working on the same git repo.

See [`docs/system-design.md`](../docs/system-design.md) in the repo root for the full
system design. This package contains:

- `cc_cloud/models.py` — SQLAlchemy 2.0 models (the SQL schema).
- `alembic/` — migrations (initial schema in `versions/0001_initial.py`).
- `cc_cloud/main.py` — FastAPI app: auth, read endpoints (drop-in compatible with the
  local explorer API) and ingest endpoints.
- `cc_cloud/ingest.py` — the shared ingest service (sessions, commits, cursors).
- `cc_cloud/worker.py` — git-native ingest worker: mirror clones, reads commit
  context from `refs/notes/claude-context`, upserts commits + contexts (webhook +
  `cc-cloud-worker backfill` cron).
- `cc_cloud/sync.py` — the `cc-cloud` CLI that uploads a dev machine's local data.

## Development

```sh
uv sync                          # install deps (uses ../cli for parsing)
uv run alembic upgrade head      # create the schema (SQLite by default)
uv run uvicorn cc_cloud.main:app --reload --port 8000
```

Production: set `CC_CLOUD_DATABASE_URL=postgresql+psycopg://…` and run migrations
with the same URL. Run tests with:

```sh
uv run python tests/smoke.py        # 43 in-process API checks
uv run python tests/e2e_cli.py      # live server + real CLI sync
uv run python tests/worker_test.py  # 19 git-webhook ingest checks
```

## Git-native ingest (zero install)

Point a GitHub webhook at `POST /api/webhooks/github` (set `CC_CLOUD_WEBHOOK_SECRET`
and configure the same secret on the webhook). Every push is fetched and new commits
plus their `refs/notes/claude-context` bundles are ingested automatically. Missed
pushes are covered by `cc-cloud-worker backfill` (cron). The worker keeps bare mirror
clones in `CC_CLOUD_REPOS_DIR` (default `backend/data/repos`, gitignored) — the server
needs read access to each project's remote.

## Sync CLI

```sh
cc-cloud login --url https://cc.example.com --token cc_…   # store credentials
cc-cloud me                                                # your user + teams
cc-cloud projects add --team <slug> --repo <origin-url> --name <name>
cc-cloud sync --project /path/to/repo --sessions --commits # upload everything
cc-cloud status --project /path/to/repo                    # what's pending
```

Cursors live in `~/.config/cc-cloud/cursors/<slug>.json`; the server mirrors them in
`sync_state` per device.
