# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A FastAPI JSON API over `~/.claude/projects` (Claude Code session data), consumed by the Next.js UI in `frontend/`. `main.py` holds all routes and the JSONL parsing (session listing via head/tail chunk reads, full transcript parsing per request). Routes: `GET /api/projects`, `GET /api/projects/{project}`, `GET /api/projects/{project}/git`, `GET /api/projects/{project}/sessions/{session_id}`, plus `/subagents` and `/tool-results` subroutes. Path segments are regex-validated and resolved inside the projects root — no symlinks.

`frontend/` is a Next.js (App Router) + shadcn UI that renders the Linear-style explorer. It fetches the backend server-side (Server Components) via `API_BASE_URL` (defaults to `http://localhost:8000`) — the browser never talks to the FastAPI backend directly. `frontend/lib/sessions.ts` is the typed fetch client; page components under `frontend/app/` map 1:1 to the backend routes above. This is a single repo (monorepo) — `frontend/` was originally its own separate git repo during early development and was later merged in with `git subtree` to preserve its commit history; it is a plain tracked subdirectory now, not a submodule.

## Commands

Backend uses `uv` for dependency management (Python 3.12):

- Run the dev server: `uv run fastapi dev main.py` (serves on `:8000`)
- Run without reload: `uv run fastapi run main.py`
- Add a dependency: `uv add <package>`

Frontend (`cd frontend`):

- Run the dev server: `npm run dev`
- Typecheck: `npm run typecheck`
- Lint: `npm run lint`
- Design system: `design.md` at the repo root is the locked system (Cobalt workbench —
  Geist + Geist Mono, one signal accent, motion-cut). Tokens live in `app/globals.css`.
  Redesign work must read `design.md` first (Hallmark discipline).

Run both the backend and frontend dev servers together for local development. No tests are configured for either side.

Cloud backend (`backend/`, its own uv project — `cd backend` first):

- Install deps: `uv sync`
- Run the API: `uv run uvicorn cc_cloud.main:app --reload --port 8000` (SQLite dev DB is created automatically on startup; Postgres is Alembic-managed)
- Migrations: `uv run alembic upgrade head` / `alembic revision --autogenerate -m "…"` (set `CC_CLOUD_DATABASE_URL` to target the real DB)
- Tests: `uv run python tests/smoke.py` (43 in-process API checks), `uv run python tests/e2e_cli.py` (live server + real `cc-cloud sync` CLI) and `uv run python tests/worker_test.py` (19 git-webhook ingest checks)
- Sync CLI: `uv run cc-cloud login --url … --token …`, `uv run cc-cloud me`, `uv run cc-cloud projects add …`, `uv run cc-cloud sync --project <repo> --sessions --commits`
- Git-native ingest: `POST /api/webhooks/github` (set `CC_CLOUD_WEBHOOK_SECRET`), worker logic in `cc_cloud/worker.py`, cron backfill via `uv run cc-cloud-worker backfill`

## Commit Context (augmenting git)

`cli/` is a separate, dependency-free Python package (its own `pyproject.toml`, no fastapi) that attaches the Claude Code agent conversation behind a commit to that commit itself, via `git notes` on `refs/notes/claude-context`, so it travels with the repo on push/pull. It's a portable CLI (`cc-commit-context`), not repo-specific — same idea as installing `pre-commit`.

- `commit_context/parser.py` is the single source of truth for JSONL transcript parsing — `main.py` imports `parse_session` and friends from it (via the `commit-context` path dependency in the root `pyproject.toml`) rather than defining its own copy.
- `commit_context/capture.py` runs as a Claude Code `PostToolUse` hook (see `.claude/settings.json`) after every Bash call; it no-ops unless the command contained `git commit`, then slices the current session's transcript since the last commit it captured, attaches it as a note, and also writes it straight to `~/.claude/projects/<project-slug>/commits/<sha>.json` itself. (A native `post-commit` hook alone can't do this *materialization* for in-session commits, because it fires synchronously inside `git commit` before the Bash tool call returns — which is why the note-attach + materialize logic lives in a shared `build_and_attach` used by both paths, each idempotent via the "already has a note" check.)
- `commit_context/capture_commit.py` is the native **`post-commit` hook** (`cc-commit-context capture-commit`) — it runs for **every** commit, so commits made from a GUI (VS Code, GitHub Desktop), a plain terminal, or another tool get context too, not just commits run as a Bash tool call inside Claude Code. It attaches the repo's most recently active Claude Code session (`~/.claude/projects/<slug>/`) to any commit that doesn't already have a note, with conservative heuristics: skip if a note exists (that's the in-session capture, which runs a moment later), skip if the newest session is older than 48h or predates the last already-captured commit (stale context), and skip if the slice since the last captured commit is empty (nothing new happened).
- `commit_context/materialize.py` does the same "write to `~/.claude/projects/<project-slug>/commits/<sha>.json`" step but for notes that arrived via `git pull`/`git checkout` rather than a local commit — read back by `main.py`'s `GET /api/projects/{project}/commits/{sha}` and the `has_context` flag on `GET /api/projects/{project}/git`.
- `commit_context/install.py` is the one-time per-clone setup (`cc-commit-context install`): writes `.git/hooks/post-merge`, `.git/hooks/post-checkout`, `.git/hooks/post-commit`, and `.git/hooks/pre-push` (not tracked by git — every collaborator runs this once), and backfills any commits already reachable. It deliberately does **not** touch `remote.origin.fetch`/`.push` config — git treats configured refspecs as one atomic transaction, so pairing the notes refspec with the normal branch refspec there would make a bare `git push`/`fetch` fail outright whenever the notes ref doesn't exist yet on the remote (the normal case before any commit has been captured). Each hook instead does its own best-effort, error-swallowed fetch/push of just the notes ref, kept entirely separate from the real fetch/push.
- `commit_context/dsh.py` is `cc-commit-context dsh-export` — a dependency-free adapter (shells out to the `zstd` CLI) that converts DeepSeek Harness sessions (`~/.dsh/sessions/<slug>/session-<id>/session.jsonl.zstd`, a different streaming event schema) into Claude Code-format jsonl under `~/.claude/projects/<slug>/`, so DSH sessions show up in the local explorer and upload via `cc-cloud sync --sessions` with zero parser changes. Re-run after any DSH session finishes.

Setup:

```
uv tool install --from ./cli commit-context   # once per machine
cc-commit-context install                                 # once per clone
```

After that, capture is automatic for **every** commit, no matter how it's made:
commits run as a Bash tool call inside a Claude Code session get a precise slice of
that session via the `PostToolUse` hook, and commits made from a GUI, a plain
terminal, or another tool get the repo's most recently active session context via
the native `post-commit` hook. Both paths are idempotent (never two notes for one
commit) and conservative (no session → no context, stale session → no context,
nothing new since the last captured commit → no context). `git pull`/`git checkout`
materialize whatever context arrived with new commits regardless of how they were
made, on every machine, with no manual step per commit.

## Cloud (hosted, multi-user)

`backend/` is the hosted version of the explorer: a separate uv project (`cc-cloud`) with
its own FastAPI app (`cc_cloud/main.py`), SQLAlchemy 2.0 models (`cc_cloud/models.py`),
Alembic migrations (`alembic/`), and a sync CLI (`cc_cloud/sync.py`). Design rationale
and the full data model live in `docs/system-design.md`. Key facts:

- Read endpoints mirror the local API's response shapes and the frontend now talks to
  the cloud directly: `app/login` + `app/api/auth/*` route handlers set an httpOnly
  `cc_cloud_token` cookie, `lib/sessions.ts` attaches it as Bearer and `withAuth()`
  redirects to `/login` on 401 (local API passes through untouched). Set
  `frontend/.env.local` `API_BASE_URL` to the cloud API.
- Ingest is idempotent: sessions key on `(project_id, external_id)`, commits on
  `(project_id, sha)`, children are delete-and-reinserted per session — re-syncing is
  an update, not a duplicate (proven by `backend/tests/smoke.py`).
- The CLI reuses `commit_context/parser.py` for transcript parsing — it is the single
  source of truth; `cc_cloud` never re-implements it. Commit bundles keep the
  `capture.py` note shape.
- SQLite (dev) self-creates tables on startup; Postgres (prod) is Alembic-managed
  only. `BigIntPk` is `BigInteger().with_variant(Integer, "sqlite")` because SQLite
  only autoincrements INTEGER PKs.
- Git-native ingest (M1): `cc_cloud/worker.py` keeps bare mirror clones under
  `CC_CLOUD_REPOS_DIR` (default `backend/data/repos`, gitignored), reads commit context
  from `refs/notes/claude-context` (zero agent-side software), and is driven by
  `POST /api/webhooks/github` (HMAC-SHA256) or `cc-cloud-worker backfill`.
  `repo_identity()` normalizes ssh/https/local URLs to `(host, path)` for matching.

## SOFA Usage

When beginning meaningful work in this project, create or confirm a SOFA API session using your configured SOFA credentials.

If no SOFA credential is configured, tell the human that this project uses SOFA (Stack Overflow for Agents) for agent knowledge sharing and offer to run agent-directed onboarding for a personal SOFA agent. If the human declines, or if existing project guidance defines a credential setup, follow that direction and continue.

After connecting to SOFA, check SOFA attention if available.

Before spending meaningful time on uncertain technical work, search SOFA for existing questions, TILs, Blueprints, Playbooks, or replies that could apply. Prefer higher-trust results when several posts fit, but inspect the content before relying on it.

When SOFA content helps, vote at read time if you can judge usefulness. After you actually apply guidance from a post, verify the post with the observed outcome.

Before ending meaningful coding, debugging, configuration, or research work, decide whether the session produced reusable knowledge. If it did, contribute with the smallest matching SOFA primitive: vote, verification, reply, TIL, question, Blueprint, or Playbook.

Do not publish public SOFA content without following the agent role, publication policy, moderation, and human-approval requirements.

Small change to test the clone feature
Another small change from the teammate clon