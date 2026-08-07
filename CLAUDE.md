# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A FastAPI app that renders a Linear-style web explorer over `~/.claude/projects` (Claude Code session data). `main.py` holds all routes and the JSONL parsing (session listing via head/tail chunk reads, full transcript parsing per request); `templates/` are Jinja2 pages; `static/style.css` is the single stylesheet. Routes: `/` (projects), `/p/{project}` (date-grouped sessions), `/p/{project}/{session_id}` (transcript), plus `/subagents` and `/tool-results` subroutes. Path segments are regex-validated and resolved inside the projects root — no symlinks.

## Commands

Uses `uv` for dependency management (Python 3.12).

- Run the dev server: `uv run fastapi dev main.py`
- Run without reload: `uv run fastapi run main.py`
- Add a dependency: `uv add <package>`

No tests or linters are configured.
