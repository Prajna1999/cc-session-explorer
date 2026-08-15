"""CC Explorer Cloud — FastAPI application.

Read endpoints deliberately mirror the local explorer API (``main.py`` at the repo
root) so the existing Next.js frontend works against the cloud with minimal changes
(see docs/system-design.md §6/§9). Ingest endpoints are the new surface the sync CLI
and the git webhook worker use.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from commit_context.parser import pretty_model

from .auth import (
    get_current_user,
    hash_api_token,
    hash_password,
    new_api_token,
    require_project,
    sign_token,
    team_role,
    verify_password,
)
from .config import get_settings
from .db import engine, get_db, init_db
from .ingest import get_cursor, ingest_commits, ingest_sessions, set_cursor
from .models import (
    Commit,
    Device,
    Project,
    Session as SessionRow,
    SessionEntry,
    Subagent,
    Team,
    TeamMember,
    ToolResult,
    User,
)
from .worker import repo_identity, sync_push, verify_github_signature
from .schemas import (
    AuthOut,
    CommitsIngestIn,
    CursorIn,
    CursorOut,
    IngestResultOut,
    LoginIn,
    MeOut,
    ProjectCreateIn,
    ProjectOut,
    RegisterIn,
    SessionsIngestIn,
    TeamOut,
    TokenCreateIn,
    UserOut,
)

SAFE_SEGMENT = re.compile(r"^[\w.-]+$")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Dev convenience (SQLite): create tables if missing. Production (Postgres)
    # is managed exclusively by Alembic migrations run as a release step.
    if engine.dialect.name == "sqlite":
        init_db()
    yield


app = FastAPI(title="CC Explorer Cloud", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Serializers (mirror the local API's response shapes)
# ---------------------------------------------------------------------------


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def entry_out(row) -> dict:
    return {
        "kind": row.kind,
        "ts": row.ts.isoformat() if row.ts else None,
        "text": row.text,
        "model": row.model,
        "name": row.name,
        "arg": row.arg,
        "params": row.params,
        "result": row.result,
        "persisted": row.persisted,
        "meta": row.meta,
    }


def session_meta(row: SessionRow) -> dict:
    return {
        "title": row.title,
        "cwd": row.cwd,
        "version": row.version,
        "models": row.models or [],
        "branches": row.branches or [],
        "first": row.started_at.isoformat() if row.started_at else None,
        "last": row.ended_at.isoformat() if row.ended_at else None,
        "prompts": row.prompts,
        "tool_calls": row.tool_calls,
        "tool_counts": row.tool_counts or {},
        "thinking": row.thinking,
        "responses": row.responses,
    }


def author_out(user: User | None) -> dict | None:
    if user is None:
        return None
    return {"id": str(user.id), "name": user.name, "email": user.email}


def session_summary(row: SessionRow) -> dict:
    return {
        "id": row.external_id,
        "title": row.title or row.external_id,
        "branch": row.branch,
        "model": pretty_model(row.models[0]) if row.models else None,
        "size": fmt_size(row.size_bytes),
        "has_subagents": row.has_subagents,
        "has_tool_results": row.has_tool_results,
        "start": row.started_at.isoformat() if row.started_at else None,
        "author": author_out(row.user),
    }


def group_by_date(sessions: list[SessionRow]) -> list[dict]:
    groups: list[dict] = []
    for s in sessions:
        label = s.started_at.strftime("%A %-d %b %Y") if s.started_at else "Unknown date"
        if not groups or groups[-1]["label"] != label:
            groups.append({"label": label, "sessions": []})
        groups[-1]["sessions"].append(session_summary(s))
    return groups


# ---------------------------------------------------------------------------
# Auth / account
# ---------------------------------------------------------------------------


def _unique_slug(db: Session, model, base: str) -> str:
    candidate = base
    n = 2
    while db.query(model).filter(model.slug == candidate).first() is not None:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


@app.post("/api/auth/register", response_model=AuthOut)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email.lower()).first() is not None:
        raise HTTPException(status_code=409, detail="email already registered")
    user = User(email=body.email.lower(), name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()

    base = re.sub(r"[^a-z0-9]+", "-", body.email.split("@")[0].lower()).strip("-") or "user"
    team = Team(slug=_unique_slug(db, Team, base), name="Personal")
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))

    token = sign_token({"sub": str(user.id)}, get_settings().jwt_secret, get_settings().token_ttl_seconds)
    db.commit()
    return AuthOut(token=token, user=UserOut(id=str(user.id), email=user.email, name=user.name))


@app.post("/api/auth/login", response_model=AuthOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="account disabled")
    token = sign_token({"sub": str(user.id)}, get_settings().jwt_secret, get_settings().token_ttl_seconds)
    return AuthOut(token=token, user=UserOut(id=str(user.id), email=user.email, name=user.name))


@app.post("/api/auth/tokens")
def create_token(body: TokenCreateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from .models import ApiToken

    plaintext = new_api_token()
    token = ApiToken(
        user_id=user.id,
        name=body.name,
        token_hash=hash_api_token(plaintext),
        token_prefix=plaintext[:12],
        scopes=["sync", "read"],
    )
    db.add(token)
    db.commit()
    return {"id": str(token.id), "name": token.name, "prefix": token.token_prefix,
            "token": plaintext, "note": "store this now — it is shown only once"}


@app.get("/api/me", response_model=MeOut)
def me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(Team, TeamMember.role)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(TeamMember.user_id == user.id)
        .order_by(Team.name)
        .all()
    )
    return MeOut(
        user=UserOut(id=str(user.id), email=user.email, name=user.name),
        teams=[TeamOut(id=str(t.id), slug=t.slug, name=t.name, role=role) for t, role in rows],
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def _project_out(db: Session, project: Project) -> ProjectOut:
    count, last = (
        db.query(func.count(SessionRow.id), func.max(func.coalesce(SessionRow.ended_at, SessionRow.started_at)))
        .filter(SessionRow.project_id == project.id)
        .first()
    )
    team = db.get(Team, project.team_id)
    return ProjectOut(
        id=str(project.id),
        team_id=str(project.team_id),
        team_name=team.name if team else None,
        slug=project.slug,
        # Local-API contract: `name` is the URL-safe slug (the frontend uses it in
        # routes and as option values); the human-readable name lives in `label`.
        name=project.slug,
        repo_url=project.repo_url,
        default_branch=project.default_branch,
        count=count or 0,
        last=last.isoformat() if last else None,
        label=project.name,
        cwd=project.repo_url,
    )


@app.get("/api/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    team_ids = [t.team_id for t in db.query(TeamMember.team_id).filter(TeamMember.user_id == user.id)]
    if not team_ids:
        return []
    projects = db.query(Project).filter(Project.team_id.in_(team_ids)).order_by(Project.name).all()
    return [_project_out(db, p) for p in projects]


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(
    body: ProjectCreateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    team_id = UUID(body.team_id)
    role = team_role(db, team_id, user.id)
    if role is None:
        raise HTTPException(status_code=403, detail="not a member of this team")
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="owner or admin role required to add projects")

    existing = (
        db.query(Project)
        .filter(Project.team_id == team_id, Project.repo_url == body.repo_url)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="project already exists for this team")

    slug = body.slug or _unique_slug(db, Project, re.sub(r"[^a-z0-9._-]+", "-", body.name.lower()).strip("-") or "project")
    if not SAFE_SEGMENT.match(slug):
        raise HTTPException(status_code=422, detail="slug must match [\\w.-]+")
    if db.query(Project).filter(Project.slug == slug).first() is not None:
        raise HTTPException(status_code=409, detail=f"slug '{slug}' is already taken — pick another")
    project = Project(
        team_id=team_id,
        slug=slug,
        name=body.name,
        repo_url=body.repo_url,
        default_branch=body.default_branch,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_out(db, project)


@app.get("/api/projects/resolve")
def resolve_project(
    repo_url: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Look up the project for a repo URL (used by the sync CLI)."""
    team_ids = [t.team_id for t in db.query(TeamMember.team_id).filter(TeamMember.user_id == user.id)]
    project = (
        db.query(Project)
        .filter(Project.team_id.in_(team_ids), Project.repo_url == repo_url)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="no project for this repo — create one first")
    return _project_out(db, project)


@app.get("/api/projects/{slug}", response_model=dict)
def project_view(slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = require_project(db, slug, user)
    sessions = (
        db.query(SessionRow)
        .filter(SessionRow.project_id == project.id)
        .order_by(SessionRow.started_at.desc().nullslast())
        .all()
    )
    branches = sorted({s.branch for s in sessions if s.branch})
    return {"groups": group_by_date(sessions), "total": len(sessions),
            "local_branches": branches, "remote_branches": []}


@app.get("/api/projects/{slug}/members")
def project_members(slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = require_project(db, slug, user)
    members = (
        db.query(User)
        .join(TeamMember, TeamMember.user_id == User.id)
        .filter(TeamMember.team_id == project.team_id)
        .order_by(User.name)
        .all()
    )
    return {"members": [author_out(m) for m in members]}


# ---------------------------------------------------------------------------
# Git history & commit context
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/git")
def git_history(slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = require_project(db, slug, user)
    commits = (
        db.query(Commit)
        .filter(Commit.project_id == project.id)
        .order_by(Commit.authored_at.desc().nullslast())
        .limit(300)
        .all()
    )
    return {
        "cwd": project.repo_url,
        "commits": [
            {
                "hash": c.short_sha,
                "sha": c.sha,
                "refs": c.refs or [],
                "author": c.author_name,
                "author_email": c.author_email,
                "date": c.authored_at.strftime("%d %b %Y %H:%M") if c.authored_at else None,
                "subject": c.subject,
                "has_context": c.has_context,
            }
            for c in commits
        ],
    }


@app.get("/api/projects/{slug}/commits/{sha}")
def commit_context(slug: str, sha: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = require_project(db, slug, user)
    commit = (
        db.query(Commit)
        .filter(Commit.project_id == project.id, Commit.sha.like(f"{sha}%"))
        .first()
    )
    if commit is None or commit.context is None:
        raise HTTPException(status_code=404, detail="no context for this commit")
    ctx = commit.context
    return {
        "session_id": ctx.session.external_id if ctx.session else ctx.session_id,
        "branch": ctx.branch,
        "entries": ctx.entries or [],
        "meta": ctx.meta or {},
    }


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@app.get("/api/projects/{slug}/sessions/{session_id}")
def session_view(slug: str, session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = require_project(db, slug, user)
    row = (
        db.query(SessionRow)
        .filter(SessionRow.project_id == project.id, SessionRow.external_id == session_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    entries = (
        db.query(SessionEntry)
        .filter(SessionEntry.session_id == row.id)
        .order_by(SessionEntry.seq)
        .all()
    )
    return {
        "entries": [entry_out(e) for e in entries],
        "meta": session_meta(row),
        "has_subagents": row.has_subagents,
        "has_tool_results": row.has_tool_results,
        "author": author_out(row.user),
    }


@app.get("/api/projects/{slug}/sessions/{session_id}/subagents")
def subagents_view(slug: str, session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = require_project(db, slug, user)
    row = (
        db.query(SessionRow)
        .filter(SessionRow.project_id == project.id, SessionRow.external_id == session_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    agents = db.query(Subagent).filter(Subagent.session_id == row.id).order_by(Subagent.created_at).all()
    return [
        {
            "id": a.external_id.removeprefix("agent-"),
            "type": a.agent_type,
            "description": a.description,
            "size": fmt_size(a.size_bytes),
        }
        for a in agents
    ]


@app.get("/api/projects/{slug}/sessions/{session_id}/subagents/{agent_id}")
def subagent_view(
    slug: str, session_id: str, agent_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    project = require_project(db, slug, user)
    row = (
        db.query(SessionRow)
        .filter(SessionRow.project_id == project.id, SessionRow.external_id == session_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    external = agent_id if agent_id.startswith("agent-") else f"agent-{agent_id}"
    sub = (
        db.query(Subagent)
        .filter(Subagent.session_id == row.id, Subagent.external_id == external)
        .first()
    )
    if sub is None:
        raise HTTPException(status_code=404, detail="subagent not found")
    from .models import SubagentEntry

    entries = db.query(SubagentEntry).filter(SubagentEntry.subagent_id == sub.id).order_by(SubagentEntry.seq).all()
    meta = {
        "title": f"{sub.agent_type} · {sub.description or agent_id}",
        "models": [],
        "branches": [],
        "first": None,
        "last": None,
        "prompts": 0,
        "tool_calls": 0,
        "tool_counts": {},
        "thinking": 0,
        "responses": 0,
    }
    return {"entries": [entry_out(e) for e in entries], "meta": meta}


@app.get("/api/projects/{slug}/sessions/{session_id}/tool-results")
def tool_results_view(
    slug: str, session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    project = require_project(db, slug, user)
    row = (
        db.query(SessionRow)
        .filter(SessionRow.project_id == project.id, SessionRow.external_id == session_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    files = db.query(ToolResult).filter(ToolResult.session_id == row.id).order_by(ToolResult.name).all()
    return [{"name": f.name, "size": fmt_size(f.size_bytes)} for f in files]


@app.get("/api/projects/{slug}/sessions/{session_id}/tool-results/{name}")
def tool_result_file(
    slug: str, session_id: str, name: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    project = require_project(db, slug, user)
    row = (
        db.query(SessionRow)
        .filter(SessionRow.project_id == project.id, SessionRow.external_id == session_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    file = (
        db.query(ToolResult)
        .filter(ToolResult.session_id == row.id, ToolResult.name == name)
        .first()
    )
    if file is None or file.content is None:
        raise HTTPException(status_code=404, detail="file not found")
    return PlainTextResponse(file.content)


# ---------------------------------------------------------------------------
# Ingest (CLI / worker)
# ---------------------------------------------------------------------------


@app.post("/api/ingest/sessions", response_model=IngestResultOut)
def ingest_sessions_route(
    body: SessionsIngestIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    project = require_project(db, body.project, user)
    return ingest_sessions(db, project, user, body)


@app.post("/api/ingest/commits")
def ingest_commits_route(
    body: CommitsIngestIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    project = require_project(db, body.project, user)
    return ingest_commits(db, project, user, body)


def _resolve_device(db: Session, user: User, hostname: str) -> Device:
    from .ingest import get_or_create_device
    from .schemas import DeviceIn

    return get_or_create_device(db, user, DeviceIn(hostname=hostname))


@app.get("/api/ingest/cursor", response_model=CursorOut)
def cursor_get(
    project: str,
    kind: str,
    device: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj = require_project(db, project, user)
    dev = _resolve_device(db, user, device)
    state = get_cursor(db, proj, dev, kind)
    if state is None:
        return CursorOut(project=project, kind=kind, last_marker=None, last_synced_at=None)
    return CursorOut(project=project, kind=kind, **state)


@app.post("/api/ingest/cursor", response_model=CursorOut)
def cursor_set(body: CursorIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proj = require_project(db, body.project, user)
    dev = _resolve_device(db, user, body.device)
    state = set_cursor(db, proj, dev, body.kind, body.last_marker)
    return CursorOut(project=body.project, kind=body.kind, **state)


# ---------------------------------------------------------------------------
# Git webhook (M1: zero-install commit context)
# ---------------------------------------------------------------------------


@app.post("/api/webhooks/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    """Receive a GitHub push event and ingest new commits + their context notes.

    Authenticated by the shared webhook secret (HMAC-SHA256, ``X-Hub-Signature-256``),
    not by a user token — the worker ingests on behalf of the project's team. See
    ``cc_cloud/worker.py`` for the git plumbing.
    """
    settings = get_settings()
    if not settings.webhook_secret:
        raise HTTPException(status_code=503, detail="webhook not configured (set CC_CLOUD_WEBHOOK_SECRET)")
    body = await request.body()
    if not verify_github_signature(body, request.headers.get("x-hub-signature-256"), settings.webhook_secret):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON payload")

    if event.get("ref", "").startswith("refs/notes/"):
        return {"accepted": False, "reason": "notes ref"}

    repo = event.get("repository") or {}
    clone_url = repo.get("clone_url") or repo.get("html_url")
    if not clone_url:
        return {"accepted": False, "reason": "no repository url"}

    # Match by exact URL first, then by normalized (host, path) identity so an
    # https clone_url finds a project registered with an ssh remote.
    project = db.query(Project).filter(Project.repo_url == clone_url).first()
    if project is None:
        ident = repo_identity(clone_url)
        project = next(
            (p for p in db.query(Project).all() if repo_identity(p.repo_url) == ident),
            None,
        )
    if project is None:
        raise HTTPException(status_code=404, detail="no project for this repository — add it to a team first")

    summary = sync_push(
        db, project,
        before=event.get("before", ""),
        after=event.get("after", ""),
        ref=event.get("ref", ""),
    )
    return {"accepted": True, **summary}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
