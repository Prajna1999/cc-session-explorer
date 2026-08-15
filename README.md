# CC Session Explorer

Explore Claude Code session history — a Linear-style UI over `~/.claude/projects` —
and attach the agent conversation behind each `git commit` to the commit itself via
git notes, so context travels with the repo and shows up for everyone on the team.

Three pieces, each independently deployable:

| Piece | Where | What it does |
|---|---|---|
| **Local explorer** | `main.py` (repo root) | FastAPI JSON API over `~/.claude/projects` on your machine |
| **UI** | `frontend/` | Next.js (App Router) + shadcn UI; server-side fetches via `API_BASE_URL` — works against the local **or** cloud API |
| **Commit context CLI** | `cli/` | Dependency-free `cc-commit-context` CLI: captures agent context onto `refs/notes/claude-context` at commit time |
| **Cloud backend** | `backend/` | Hosted, multi-user FastAPI: teams, auth, session/commit ingest, git-webhook ingestion (`cc-cloud` + `cc-cloud-worker` CLIs) |

## Prerequisites

- **Python ≥ 3.12** with [uv](https://docs.astral.sh/uv/)
- **Node ≥ 20** and npm
- Claude Code sessions on your machine (for the local explorer / capture)

---

## Quick start — local explorer (2 terminals)

```sh
# terminal 1 — API on :8000 (serves ~/.claude/projects)
uv sync
uv run fastapi dev main.py

# terminal 2 — UI on :3000
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. You'll see every project with Claude Code session
history on this machine — transcripts, subagent runs, and tool results.

---

## Local dev — the pieces in detail

### Frontend (`frontend/`)

```sh
cd frontend
npm install
npm run dev          # dev server (default http://localhost:3000)
npm run typecheck    # tsc --noEmit
npm run lint         # eslint
npm run build        # production build
npm start            # serve the production build
```

The UI never talks to the backend from the browser — pages are Server Components
that fetch via `API_BASE_URL` (default `http://localhost:8000`). Point it at the
cloud backend with a local override:

```sh
# frontend/.env.local
API_BASE_URL=http://localhost:8100
```

### Local API (`main.py`)

`GET /api/projects` lists projects; everything else mirrors it (see [API
reference](#api-reference)). Route path segments are regex-validated and resolved
inside the projects root — no symlink escapes.

### Commit context (`cli/`)

Install once per machine, once per clone:

```sh
uv tool install --from ./cli commit-context   # once per machine
cc-commit-context install                      # once per clone
```

That writes four git hooks:

- **`post-commit` capture (native)**: runs for **every** commit, so commits made
  from a GUI (VS Code, GitHub Desktop), a plain terminal, or another tool get
  context too — not just commits run inside Claude Code. It attaches the repo's
  most recently active Claude Code session
  (`~/.claude/projects/<project-slug>/`) to any commit that doesn't already have
  a note, and writes it to `~/.claude/projects/<project-slug>/commits/<sha>.json`.
- **In-session capture (via Claude Code `PostToolUse`)**: after every Bash call
  in a Claude Code session, the capture hook checks whether the command contained
  `git commit`. If it did, it slices that session's transcript since the last
  captured commit and stores it as a git note on `refs/notes/claude-context`
  (plus the same `commits/<sha>.json` materialization). Idempotent with the
  native hook — a commit never gets two notes.
- **`post-merge` / `post-checkout` materialize**: when new commits arrive via
  `git pull` / `git checkout`, their notes (if any) are written to the same
  `commits/<sha>.json` location — so context shows up on every machine, no
  manual step.
- **`pre-push`**: pushes the notes ref alongside your branch.

> Capture is conservative by design: no Claude Code session for the repo → no
> context; a session older than 48h, or one that predates the last captured
> commit → no context; nothing new since the last captured commit → no context.
> In-session commits get a precise slice of that session; out-of-session commits
> get the most recently active session's context.

Once notes exist on the remote, teammates see the context with zero agent-side
software — see [Git-native ingest](#git-native-ingest-m1).

### Cloud backend (`backend/`)

Hosted, multi-user version of the same explorer: teams share session logs and
per-commit agent context of everyone working on the same repo. Its own uv project:

```sh
cd backend
uv sync
uv run uvicorn cc_cloud.main:app --reload --port 8000   # dev; SQLite DB auto-created at backend/cc_cloud.db
```

Production: set `CC_CLOUD_DATABASE_URL=postgresql+psycopg://…` and migrate:

```sh
uv run alembic upgrade head
```

#### Sign up, add a project, sync

1. **Create your account** — open the UI's `/login` page and use the *Register* tab,
   or:

   ```sh
   curl -X POST http://localhost:8000/api/auth/register \
     -H 'Content-Type: application/json' \
     -d '{"email":"you@example.com","password":"hunter2hunter2"}'
   ```

   Your personal team is auto-created (slug = your handle, e.g. `you`).

2. **Create an API token** for the CLI (shown once):

   ```sh
   # login to get a session token
   TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"email":"you@example.com","password":"hunter2hunter2"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

   # mint a cc_… API token
   curl -X POST http://localhost:8000/api/auth/tokens \
     -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"name":"my-cli"}'
   ```

3. **Point the CLI at the server and add your repo**:

   ```sh
   uv run cc-cloud login --url http://localhost:8000 --token cc_…
   uv run cc-cloud me
   uv run cc-cloud projects add --team you --repo git@github.com:you/repo.git --name "My repo"
   ```

4. **Upload a machine's data** (incremental — cursors under
   `~/.config/cc-cloud/cursors/<slug>.json` make re-runs updates, not duplicates):

   ```sh
   uv run cc-cloud sync --project /path/to/repo --sessions --commits
   uv run cc-cloud status --project /path/to/repo   # what a sync would upload
   ```

   Every team member can run the same `sync` for the shared repo; the team sees
   everyone's sessions and commit context.

#### Git-native ingest (M1)

Commit context rides git notes, so the server can ingest it with **zero agent-side
software**: it keeps a bare mirror clone of each project and reads
`refs/notes/claude-context` directly.

1. Add the repo as a project (above).
2. Set `CC_CLOUD_WEBHOOK_SECRET` and point a GitHub webhook at
   `POST /api/webhooks/github` (HMAC-SHA256 signed payloads).
3. Every push is fetched and new commits + their notes bundles are ingested
   automatically. Missed pushes are covered by a cron backfill:

   ```sh
   uv run cc-cloud-worker backfill
   ```

Mirror clones live under `CC_CLOUD_REPOS_DIR` (default `backend/data/repos`,
gitignored); the server needs read access to each project's remote.

#### Tests

```sh
uv run python tests/smoke.py        # 43 in-process API checks
uv run python tests/e2e_cli.py      # live server + real cc-cloud sync CLI
uv run python tests/worker_test.py  # 19 git-webhook ingest checks
```

---

## API reference

### Local API (`main.py`, :8000)

| Route | Returns |
|---|---|
| `GET /api/projects` | projects (slug, display name, git info) |
| `GET /api/projects/{project}` | project summary + sessions |
| `GET /api/projects/{project}/git` | commits with `has_context` flags |
| `GET /api/projects/{project}/commits/{sha}` | commit context bundle (entries + meta) |
| `GET /api/projects/{project}/sessions/{session_id}` | full transcript |
| `…/subagents`, `…/tool-results` | per-session subagent / tool-result views |

### Cloud API (`backend/cc_cloud/main.py`)

Same read shapes as the local API (drop-in for the frontend), plus:

| Route | Purpose |
|---|---|
| `POST /api/auth/register`, `POST /api/auth/login` | accounts; httpOnly `cc_cloud_token` cookie |
| `POST /api/auth/tokens` | mint a `cc_…` API token (sync CLI) |
| `GET /api/me` | your user + teams |
| `GET/POST /api/projects` | list / create projects (team-scoped) |
| `GET /api/projects/{slug}/members` | team membership |
| `POST /api/ingest/sessions`, `POST /api/ingest/commits` | idempotent ingest (keys: project + external_id / sha) |
| `GET/POST /api/ingest/cursor` | per-device sync cursors |
| `POST /api/webhooks/github` | M1 git-native ingest (HMAC-SHA256) |
| `GET /healthz` | liveness |

All reads are scoped by team membership — non-members get `403`.

---

## More docs

- `docs/system-design.md` — full system design (data model, milestones M0–M4)
- `backend/README.md` — cloud backend + CLI reference
- `cli/README.md` — commit-context CLI
- `CLAUDE.md` — agent-facing project guide
