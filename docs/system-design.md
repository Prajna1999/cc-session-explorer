# CC Explorer Cloud — System Design

Status: **Draft v1** — proposed architecture for turning the local CC (Claude Code) session
explorer into a hosted, multi-user web app where a team can see each other's agent
context (session logs and per-commit context) for shared git repos.

## 1. Context & goals

### 1.1 Today (as-is)

- A FastAPI JSON API (`main.py`) serves **one machine's** `~/.claude/projects` — the
  Claude Code transcript store for that user. Routes: projects, sessions, subagents,
  tool-results, git history, per-commit context.
- A Next.js (App Router) + shadcn frontend renders the explorer; server components call
  the API via `API_BASE_URL` (defaults to `http://localhost:8000`).
- `commit-context/` attaches the agent conversation behind each `git commit` to the
  commit itself via `refs/notes/claude-context`, so context travels with the repo on
  push/pull. `capture.py` fires as a Claude Code `PostToolUse` hook after in-session
  `git commit` Bash calls; `materialize.py` writes notes out to
  `~/.claude/projects/<slug>/commits/<sha>.json` on every machine after pull/checkout.

### 1.2 What we are building

A hosted version of the explorer: users sign in, join teams, and for each shared git
repo they can browse **their own and their teammates'** agent sessions and the
per-commit agent context. The two sources of truth stay exactly as they are today —
local transcripts and git notes — and a cloud ingestion pipeline moves the data into a
central SQL store.

### 1.3 Goals

| Goal | Detail |
|---|---|
| Team visibility | Any team member can see who worked on a repo, when, and what the agent did (prompts, tool calls, outputs). |
| Repo-native context | Commit context keeps riding `refs/notes/claude-context`; the cloud ingests it with **zero extra agent-side software** (webhook + clone + materialize). |
| Full logs (opt-in) | A small `cc-cloud sync` CLI uploads full session transcripts from a dev machine for richer browsing. |
| Drop-in API contract | Read endpoints keep the same shapes as the local API so the existing Next.js frontend works with minimal changes. |
| Multi-tenant & secure | Users, teams, role-based access; transcripts are sensitive (source code, commands, possibly secrets). |

### 1.4 Non-goals (v1)

- No remote control / live collaboration on running agents.
- No code execution or CI integration beyond the git webhook.
- No billing/quotas, no public sharing (everything is team-private).

## 2. Data inventory (what needs to move)

| Source | Where it lives | Shape | Notes |
|---|---|---|---|
| Session transcripts | `~/.claude/projects/<slug>/<session-id>.jsonl` | JSONL of `user/assistant/system/attachment/ai-title/…` events | Full fidelity; parsed by `commit_context.parser.parse_session` (single source of truth). |
| Subagent transcripts | `<session-id>/subagents/agent-*.jsonl` + `agent-*.meta.json` | JSONL + meta JSON | Same parse path. |
| Tool results | `<session-id>/tool-results/<name>` | raw text files | Can be large. |
| Commit context bundles | `commits/<sha>.json` (= git note body) | JSON `{schema_version, commit, session_id, branch, captured_at, entries[], meta{}}` | Already portable; travels via git. |
| Git metadata | local `.git` | sha, author, subject, date, refs, parents | Needed in cloud to render the git page. |

Key constraint: **transcripts exist only on the machine where the agent ran.** Git notes
give us a repo-native channel for commit-tied context; anything more (full sessions)
requires an agent-side uploader.

## 3. Target architecture

```
 ┌────────────────────────────── Dev machines ──────────────────────────────┐
 │                                                                          │
 │  Claude Code session ── PostToolUse hook ──► cc-commit-context capture  │
 │       │                                          │ (git notes)           │
 │       │                                          ▼                      │
 │  ~/.claude/projects/<slug>/*.jsonl      refs/notes/claude-context       │
 │       │                                          │                       │
 │       │ cc-cloud sync (opt-in)                  │ git push               │
 │       ▼                                          ▼                       │
 │  HTTPS batch upload                       Git host (GitHub/GitLab)      │
 └───────────────┬──────────────────────────────────┬───────────────────────┘
                 │                                  │ push webhook
                 ▼                                  ▼
        ┌─────────────────────────────────────────────────────────┐
        │                    Cloud (single region)                │
        │                                                         │
        │  ┌──────────────┐   ┌──────────────┐   ┌─────────────┐  │
        │  │ Next.js UI   │──►│ Cloud API    │──►│  Postgres   │  │
        │  │ (frontend/)  │   │ (FastAPI)    │   │  (managed)  │  │
        │  └──────────────┘   └──────┬───────┘   └──────┬──────┘  │
        │                            │                  ▲         │
        │                            ▼                  │         │
        │  ┌──────────────┐   ┌──────────────┐          │         │
        │  │ Ingest worker│──►│   API ingest │──────────┘         │
        │  │ (clone+notes)│   │  endpoints   │                    │
        │  └──────────────┘   └──────────────┘                    │
        │                                                         │
        │  Object storage (S3-compatible): raw JSONL + big tool   │
        │  results, referenced by `raw_key` / `storage_key`.      │
        └─────────────────────────────────────────────────────────┘
```

### Components

1. **Cloud API** (`cloud/cc_cloud/main.py`) — FastAPI app. Auth (JWT + API tokens),
   read endpoints (same contract as the local API), ingest endpoints, cursor endpoints.
2. **SQL store** (`cloud/cc_cloud/models.py`) — SQLAlchemy 2.0 models, Alembic
   migrations. Postgres in production, SQLite for local dev/tests.
3. **Ingest worker** — subscribes to git-host push webhooks; clones/fetches the repo,
   fetches `refs/notes/claude-context`, materializes note bundles (reuses
   `commit_context.materialize` logic) and upserts commits + contexts via the API or
   directly against the DB.
4. **Sync CLI** (`cloud/cc_cloud/sync.py`) — opt-in uploader for full session
   transcripts from a dev machine. Reuses `commit_context.parser.parse_session`.
5. **Frontend** — the existing Next.js app, pointed at the cloud API, with an auth
   layer and a team/project switcher (see §9).
6. **Object storage** — raw JSONL bodies and large tool-result files; DB holds parsed,
   bounded content.

## 4. Data flow & ingestion paths

### Path A — Git-native commit context (primary, zero install)

1. Teammate commits in a Claude Code session → `capture.py` attaches the conversation
   slice as a git note on `refs/notes/claude-context` (already shipped) and materializes
   `commits/<sha>.json` locally.
2. `git push` uploads the notes ref (pre-push hook, already shipped).
3. Git host fires a push webhook → `POST /api/webhooks/github` (HMAC-SHA256 verified
   with `CC_CLOUD_WEBHOOK_SECRET`) → the worker (`cloud/cc_cloud/worker.py`) fetches
   the project's mirror clone, reads each new commit's note bundle + git metadata,
   and upserts `commits` + `commit_contexts`. Repos are matched by normalized
   `(host, path)` identity, so an https webhook URL finds a project registered with
   an ssh remote.
4. A periodic re-sync covers missed pushes: `cc-cloud-worker backfill` (cron) does a
   full `rev-list --branches --tags` diff against known shas and ingests everything
   new.

**Implemented and verified (M1):** `cloud/tests/worker_test.py` (19 checks: identity
normalization, note ingestion from a mirror clone, idempotent re-sync, webhook
signature accept/reject, incremental push path). A live backfill of this repo ingested
11 commits and recovered all 3 notes from `refs/notes/claude-context`.

This path captures exactly the "context behind each commit" — no new agent-side
software beyond what `cc-commit-context install` already sets up.

### Path B — Full session sync (opt-in CLI)

`cc-cloud sync --project <path>`:

1. Resolves repo root, repo URL, local project dir `~/.claude/projects/<slug>/`.
2. Reads a local cursor (per repo: last-synced mtime per jsonl, last-synced SHA for
   commits), parses new/updated files with `parse_session`, posts batched payloads to
   `POST /api/ingest/sessions` and `POST /api/ingest/commits`.
3. Server upserts idempotently (unique keys below), updates per-device `sync_state`,
   and records an `ingest_run` for audit.

Idempotency: sessions keyed on `(project_id, external_id)`; entries on
`(session_id, seq)`; subagents on `(session_id, external_id)`; tool results on
`(session_id, name)`; commits on `(project_id, sha)`; context 1:1 on `commit_id`.
Re-syncing the same data is a no-op update, so `cron`-style frequent sync is safe.

### Path C — (later) manual upload

Drag a `.jsonl` into the UI to import a session you have locally but whose machine
isn't syncing. Same ingest path as B.

## 5. Data model (SQL)

Full DDL lives in `cloud/alembic/versions/` (generated from `cloud/cc_cloud/models.py`).
Overview:

```
users ──< team_members >── teams ──< projects ──< sessions ──< session_entries
  │              │                     │            ├──< subagents ──< subagent_entries
  │              │                     │            └──< tool_results
  │              │                     ├──< commits ──< commit_contexts (1:1)
  │              │                     └──< sync_state (device, kind)
  ├──< devices ──┘
  ├──< api_tokens
  └──< ingest_runs
```

| Table | Purpose | Key columns / constraints | Notes |
|---|---|---|---|
| `users` | Human accounts | `email` unique, `password_hash`, `name`, `is_active` | Password via `hashlib.scrypt` (stdlib) in v1; OAuth later. |
| `teams` | A workspace of people sharing repos | `slug` unique, `name` | |
| `team_members` | Membership + role | UNIQUE(`team_id`,`user_id`), `role` ∈ owner/admin/member | All reads are scoped through membership. |
| `projects` | One git repo | UNIQUE(`team_id`,`repo_url`), `slug` unique (route-safe `[\w.-]+`), `name`, `default_branch` | Slug defaults to repo name, suffixed on collision. |
| `devices` | A dev machine that ingests | `user_id`, `hostname`, `os`, `claude_version`, `last_seen_at` | Provenance for sessions; enables "whose machine". |
| `sessions` | One Claude Code transcript | UNIQUE(`project_id`,`external_id`); `user_id`, `device_id`, `started_at`/`ended_at`, `models`/`branches`/`tool_counts` JSONB, counters, `size_bytes`, `has_subagents`, `has_tool_results`, `raw_key` | `external_id` = local jsonl stem. `raw_key` points at object storage copy of the full jsonl. |
| `session_entries` | Parsed transcript rows | UNIQUE(`session_id`,`seq`); `kind` ∈ branch/user/assistant/thinking/tool/system; `ts`, `text`, `model`, `name`, `arg`, `params` JSONB, `result`, `persisted`, `meta` | Normalized so transcripts are queryable/streamable; mirrors parser output 1:1. |
| `subagents` | Subagent run inside a session | UNIQUE(`session_id`,`external_id`); `agent_type`, `description`, `size_bytes` | |
| `subagent_entries` | Subagent transcript rows | UNIQUE(`subagent_id`,`seq`) | Same column shape as `session_entries`. |
| `tool_results` | Persisted tool-result files | UNIQUE(`session_id`,`name`); `size_bytes`, `content` TEXT or `storage_key` | Big content goes to object storage. |
| `commits` | Git commits of a project | UNIQUE(`project_id`,`sha`); `subject`, `author_name`/`author_email`, `authored_at`, `refs` JSONB, `parents` JSONB, `has_context` | Ingested from `git log` + webhook. |
| `commit_contexts` | The note bundle for a commit | UNIQUE(`commit_id`); `session_id` (link to `sessions.external_id` when resolvable), `branch`, `schema_version`, `captured_at`, `meta` JSONB, `entries` JSONB, `raw` TEXT | `entries` kept as JSONB: bounded, append-only slices, read-mostly. |
| `api_tokens` | CLI/automation credentials | `token_hash` (sha256) unique, `token_prefix`, `scopes` JSONB, `revoked_at`, `last_used_at` | Plaintext shown once at creation. |
| `sync_state` | Per-device ingest cursors | UNIQUE(`device_id`,`project_id`,`kind`) | Server-side mirror of the CLI's local cursor; enables resumable sync. |
| `ingest_runs` | Audit of every ingest | `device_id`, `project_id`, `kind`, `status`, counters (`sessions_added`, `entries_added`, …), `error` | Observability + security audit. |

### Design decisions

- **UUID PKs for public entities** (users, teams, projects, sessions, commits, …) —
  no enumeration, easy external references. **Bigint identity PKs for entry tables**
  (high cardinality, insert-heavy, never referenced externally).
- **JSONB for bounded, read-mostly structures** (`tool_counts`, `refs`, `parents`,
  `params`, commit-context `entries`/`meta`) — matches the existing bundle format,
  avoids a join explosion for the transcript viewer. **Normalized rows for the actual
  transcript entries** so we can paginate, search, and stream a 3 MB jsonl without
  loading one giant JSONB column.
- **`DateTime(timezone=True)` everywhere**; `parse_ts` output (aware datetimes) is
  stored as-is.
- **Portable types**: `Uuid`, `JSON().with_variant(JSONB, "postgresql")`, booleans with
  `server_default` — the same models run against SQLite (dev/tests) and Postgres (prod).
- Raw jsonl is **not** stored in the DB; `sessions.raw_key` references object storage.

## 6. API design

Base path `/api`. All read + ingest endpoints require auth (JWT Bearer for UI, API
token for CLI). Responses mirror the local API shapes (§"drop-in" goal).

**Auth / account**

| Method | Path | Body → Result |
|---|---|---|
| POST | `/api/auth/register` | `{email, name, password}` → `{token, user}` |
| POST | `/api/auth/login` | `{email, password}` → `{token, user}` |
| POST | `/api/auth/tokens` | `{name}` → `{token_plaintext (once), prefix, id}` |
| GET | `/api/me` | → `{user, teams: [{id, slug, name, role}]}` |

**Read (same contract as local API)**

| Method | Path | Notes |
|---|---|---|
| GET | `/api/projects` | Projects across my teams; `{name(=slug), label, cwd(=repo_url), count, last}` |
| GET | `/api/projects/{slug}` | `{groups:[{label, sessions:[summary]}], total, local_branches, remote_branches}` — session summary gains `author` |
| GET | `/api/projects/{slug}/sessions/{session_id}` | `{entries, meta, has_subagents, has_tool_results}` + `author` |
| GET | `/api/projects/{slug}/sessions/{id}/subagents` / `…/{agent_id}` | subagent list / transcript |
| GET | `/api/projects/{slug}/sessions/{id}/tool-results` / `…/{name}` | file list / text |
| GET | `/api/projects/{slug}/git` | `{cwd, commits:[{hash, sha, refs, author, date, subject, has_context}]}` |
| GET | `/api/projects/{slug}/commits/{sha}` | `{session_id, branch, entries, meta}` (the bundle) |
| GET | `/api/projects/{slug}/members` | Users with sessions or commits in this project |
| GET | `/healthz` | liveness |

**Ingest (CLI / worker)**

| Method | Path | Body → Result |
|---|---|---|
| POST | `/api/ingest/sessions` | `{project, device:{hostname,os,claude_version}, sessions:[{external_id, title, cwd, branch, version, started_at, ended_at, models, branches, prompts, tool_calls, tool_counts, thinking, responses, size_bytes, has_subagents, has_tool_results, entries:[{seq,kind,ts,text,model,name,arg,params,result,persisted,meta}], subagents:[…], tool_results:[…]}]}` → `{accepted, updated, skipped}` |
| POST | `/api/ingest/commits` | `{project, commits:[{sha, subject, author_name, author_email, authored_at, refs, parents, context:{schema_version, session_id, branch, captured_at, entries, meta}|null}]}` → `{accepted, updated}` |
| GET/POST | `/api/ingest/cursor` | `{project, kind}` ↔ `{last_marker}` (device-scoped via token) |

## 7. Auth & authorization

- **Passwords**: `hashlib.scrypt` with per-user salt (stdlib, no heavy deps). OAuth
  (Google/GitHub) is a follow-up.
- **Sessions**: short-lived HMAC-signed token (JWT-style) for the UI.
- **CLI**: long-lived API tokens (`cc_…` prefix), stored hashed; scoped
  `["sync", "read"]`; revocable.
- **Authorization model**: every request resolves the project, then requires
  `team_members` membership with `role ≥ member` (owner/admin manage membership,
  owner deletes projects). All queries are filtered through the user's team ids — no
  cross-team leakage even if an id is guessed (UUIDs make that unlikely anyway).
- **Ingest ownership**: the uploading user (from the token) becomes
  `sessions.user_id` / `devices.user_id`; commit authors come from git and are matched
  to users by email when possible.

## 8. Deployment

- **Postgres**: managed (Neon/RDS/Supabase), TLS required, automated backups.
- **Object storage**: S3-compatible bucket (SSE-S3 encryption) for raw jsonl + large
  tool results.
- **API + worker**: containers; `docker compose` locally, Fly.io/Railway/EKS in prod.
  Migrations (`alembic upgrade head`) run as a release step, never from app startup.
- **Webhook receiver**: part of the API or a thin separate endpoint; verifies the git
  host's signature.
- **Frontend**: the existing Next.js app deployed (Vercel or same container); server
  components call the cloud API via `API_BASE_URL`; auth token supplied by a login
  page/cookie (see §9).
- **Config**: env vars (`DATABASE_URL`, `STORAGE_*`, `JWT_SECRET`, `WEBHOOK_SECRET`,
  `API_PUBLIC_URL`), no secrets in git.

## 9. Frontend changes (minimal)

**Implemented (M3):** the existing pages now work against the cloud API with auth:

- `app/login/page.tsx` — login/register form; `app/api/auth/login|register|logout/route.ts`
  proxy to the cloud API and set an httpOnly `cc_cloud_token` cookie (7-day, lax,
  secure in prod).
- `lib/sessions.ts` — every fetch attaches `Authorization: Bearer` from the cookie
  (server-side, via `next/headers`); 401s throw `AuthError` and the `withAuth()`
  helper redirects to `/login`. Against the local explorer API (no auth, no 401s)
  everything passes through untouched.
- Team-aware project switcher (`<optgroup>` per team), session rows and session pages
  show the author, a per-project Members page (`/p/[project]/members`), and a Sign
  out button in the header.

Remaining ideas: avatars/initials, a "who did this" view per commit (author is already
shown), secret-redaction notice in the UI.

## 10. Security & privacy

- Transcripts contain source code, shell commands, and potentially secrets — treat as
  sensitive PII-adjacent data: TLS everywhere, encryption at rest (object storage SSE,
  disk encryption), least-privilege API tokens, and team-scoped queries only.
- Redaction of known secret patterns (AWS keys, tokens) at ingest is a v1.1 item; note
  it in the UI that logs are visible to team members.
- `ingest_runs` + `api_tokens.last_used_at` give an audit trail of who uploaded what,
  from which device.

## 11. Operations

- **Observability**: structured JSON logs per request with `user_id`/`project_id`;
  Prometheus metrics (ingest rates, latency); Sentry for errors.
- **Retention**: keep transcripts while the team exists; deletion of a team cascades
  (hard delete v1, tombstone later).
- **Backups**: nightly Postgres dump + object-storage versioning; restore drill tested.
- **Cost model**: dominated by Postgres storage of entries (rows are small) and object
  storage for raw jsonl (a 3 MB transcript ≈ negligible); well under $100/mo for a
  10-person team.

## 12. Rollout plan

| Milestone | Scope |
|---|---|
| M0 | `cloud/` package: models + Alembic migration + API skeleton (auth, read endpoints) — **this doc's companion code** |
| M1 | **Done** — `POST /api/webhooks/github` + `cc_cloud/worker.py` (mirror clone, notes fetch, upsert) + `cc-cloud-worker backfill` cron; zero-install context for the whole team |
| M2 | `cc-cloud sync` CLI + full session ingest + cursor protocol |
| M3 | **Done** — frontend auth (login/register + httpOnly cookie), team-aware switcher, author display, members page, sign out |
| M4 | Hardening: secret redaction, OAuth, pagination for large projects, manual upload |

## 13. Open questions

- Git host support first: GitHub webhook only, or GitHub + GitLab + Gitea?
- Should commit context be the *only* public-by-default data (full transcripts
  opt-in per user), or are full sessions fine for the whole team?
- Multi-region / on-prem deployment for teams that can't use a shared cloud?
- Do we need per-project "mute" for noisy repos (huge transcripts)?

---

Companion code: `cloud/` (SQLAlchemy models, Alembic migration, cloud API, sync CLI).
