# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A FastAPI JSON API over `~/.claude/projects` (Claude Code session data), consumed by the Next.js UI in `frontend/`. `main.py` holds all routes and the JSONL parsing (session listing via head/tail chunk reads, full transcript parsing per request). Routes: `GET /api/projects`, `GET /api/projects/{project}`, `GET /api/projects/{project}/git`, `GET /api/projects/{project}/sessions/{session_id}`, plus `/subagents` and `/tool-results` subroutes. Path segments are regex-validated and resolved inside the projects root — no symlinks.

`frontend/` is a Next.js (App Router) + shadcn UI that renders the Linear-style explorer. It fetches the backend server-side (Server Components) via `API_BASE_URL` (defaults to `http://localhost:8000`) — the browser never talks to the FastAPI backend directly. `frontend/lib/sessions.ts` is the typed fetch client; page components under `frontend/app/` map 1:1 to the backend routes above.

## Commands

Backend uses `uv` for dependency management (Python 3.12):

- Run the dev server: `uv run fastapi dev main.py` (serves on `:8000`)
- Run without reload: `uv run fastapi run main.py`
- Add a dependency: `uv add <package>`

Frontend (`cd frontend`):

- Run the dev server: `npm run dev`
- Typecheck: `npm run typecheck`
- Lint: `npm run lint`

Run both the backend and frontend dev servers together for local development. No tests are configured for either side.

## Commit Context (augmenting git)

`commit-context/` is a separate, dependency-free Python package (its own `pyproject.toml`, no fastapi) that attaches the Claude Code agent conversation behind a commit to that commit itself, via `git notes` on `refs/notes/claude-context`, so it travels with the repo on push/pull. It's a portable CLI (`cc-commit-context`), not repo-specific — same idea as installing `pre-commit`.

- `commit_context/parser.py` is the single source of truth for JSONL transcript parsing — `main.py` imports `parse_session` and friends from it (via the `commit-context` path dependency in the root `pyproject.toml`) rather than defining its own copy.
- `commit_context/capture.py` runs as a Claude Code `PostToolUse` hook (see `.claude/settings.json`) after every Bash call; it no-ops unless the command contained `git commit`, then slices the current session's transcript since the last commit it captured, attaches it as a note, and also writes it straight to `~/.claude/projects/<project-slug>/commits/<sha>.json` itself — a native `post-commit` git hook can't do this materialization, because it fires synchronously inside `git commit` itself, before the Bash tool call even returns to Claude Code, let alone before `PostToolUse`/`capture` has run and attached the note.
- `commit_context/materialize.py` does the same "write to `~/.claude/projects/<project-slug>/commits/<sha>.json`" step but for notes that arrived via `git pull`/`git checkout` rather than a local commit — read back by `main.py`'s `GET /api/projects/{project}/commits/{sha}` and the `has_context` flag on `GET /api/projects/{project}/git`.
- `commit_context/install.py` is the one-time per-clone setup (`cc-commit-context install`): configures notes fetch/push refspecs on `remote.origin`, writes `.git/hooks/post-merge` and `.git/hooks/post-checkout` (not tracked by git — every collaborator runs this once), and backfills any commits already reachable.

Setup:

```
uv tool install --from ./commit-context commit-context   # once per machine
cc-commit-context install                                 # once per clone (root repo and frontend/ both — separate git repos)
```

After that, capture is automatic **only for commits made via `git commit` as a Bash tool call inside a Claude Code session** — the capture hook is a Claude Code `PostToolUse` hook, so it never fires for a commit run from a plain terminal, a different tool, or CI, even if a Claude Code session happens to be open elsewhere on the same repo. Those commits simply get no attached context, which is correct: there's no agent conversation behind them to capture. `git pull`/`git checkout` materialize whatever context arrived with new commits regardless of how they were made, on every machine, with no manual step per commit.

## SOFA Usage

When beginning meaningful work in this project, create or confirm a SOFA API session using your configured SOFA credentials.

If no SOFA credential is configured, tell the human that this project uses SOFA (Stack Overflow for Agents) for agent knowledge sharing and offer to run agent-directed onboarding for a personal SOFA agent. If the human declines, or if existing project guidance defines a credential setup, follow that direction and continue.

After connecting to SOFA, check SOFA attention if available.

Before spending meaningful time on uncertain technical work, search SOFA for existing questions, TILs, Blueprints, Playbooks, or replies that could apply. Prefer higher-trust results when several posts fit, but inspect the content before relying on it.

When SOFA content helps, vote at read time if you can judge usefulness. After you actually apply guidance from a post, verify the post with the observed outcome.

Before ending meaningful coding, debugging, configuration, or research work, decide whether the session produced reusable knowledge. If it did, contribute with the smallest matching SOFA primitive: vote, verification, reply, TIL, question, Blueprint, or Playbook.

Do not publish public SOFA content without following the agent role, publication policy, moderation, and human-approval requirements.
