"""Pydantic request/response schemas for the cloud API.

Ingest payloads mirror the output of ``commit_context.parser.parse_session`` and the
commit-context note bundle shape (see ``commit_context/capture.py``), so the sync CLI
can serialize them with almost no transformation.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Auth / account
# ---------------------------------------------------------------------------


class RegisterIn(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=120)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenCreateIn(BaseModel):
    name: str = Field(default="default", max_length=120)


class UserOut(BaseModel):
    id: str
    email: str
    name: str | None


class TeamOut(BaseModel):
    id: str
    slug: str
    name: str
    role: str


class MeOut(BaseModel):
    user: UserOut
    teams: list[TeamOut]


class AuthOut(BaseModel):
    token: str
    user: UserOut


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class ProjectCreateIn(BaseModel):
    team_id: str
    name: str = Field(max_length=240)
    repo_url: str = Field(max_length=1024)
    slug: str | None = Field(default=None, max_length=120, pattern=r"^[\w.-]+$")
    default_branch: str | None = None


class ProjectOut(BaseModel):
    id: str
    team_id: str
    team_name: str | None = None
    slug: str
    name: str
    repo_url: str
    default_branch: str | None
    count: int = 0
    last: str | None = None
    # Local-API-compatible aliases (the existing frontend reads these).
    label: str | None = None
    cwd: str | None = None


# ---------------------------------------------------------------------------
# Ingest (session transcripts)
# ---------------------------------------------------------------------------


class DeviceIn(BaseModel):
    hostname: str = ""
    os: str | None = None
    claude_version: str | None = None


class EntryIn(BaseModel):
    seq: int = 0
    kind: str
    ts: str | None = None
    text: str | None = None
    model: str | None = None
    name: str | None = None
    arg: str | None = None
    params: list[dict] | None = None
    result: str | None = None
    persisted: str | None = None
    meta: bool | None = None


class SubagentIn(BaseModel):
    external_id: str
    agent_type: str = "?"
    description: str | None = None
    size_bytes: int = 0
    entries: list[EntryIn] = []


class ToolResultIn(BaseModel):
    name: str
    size_bytes: int = 0
    content: str | None = None


class SessionIn(BaseModel):
    external_id: str
    title: str | None = None
    cwd: str | None = None
    branch: str | None = None
    version: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    models: list[str] = []
    branches: list[str] = []
    prompts: int = 0
    tool_calls: int = 0
    tool_counts: dict[str, int] = {}
    thinking: int = 0
    responses: int = 0
    size_bytes: int = 0
    has_subagents: bool = False
    has_tool_results: bool = False
    entries: list[EntryIn] = []
    subagents: list[SubagentIn] = []
    tool_results: list[ToolResultIn] = []


class SessionsIngestIn(BaseModel):
    project: str  # project slug
    device: DeviceIn = Field(default_factory=DeviceIn)
    sessions: list[SessionIn] = []


class IngestResultOut(BaseModel):
    accepted: int = 0
    updated: int = 0
    skipped: int = 0
    entries_added: int = 0
    subagents_added: int = 0
    tool_results_added: int = 0


# ---------------------------------------------------------------------------
# Ingest (commits + commit context)
# ---------------------------------------------------------------------------


class CommitContextIn(BaseModel):
    schema_version: int = 1
    session_id: str | None = None
    branch: str | None = None
    captured_at: str | None = None
    entries: list[EntryIn] = []
    meta: dict | None = None


class CommitIn(BaseModel):
    sha: str = Field(min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")
    subject: str | None = None
    author_name: str | None = None
    author_email: str | None = None
    authored_at: str | None = None
    refs: list[str] = []
    parents: list[str] = []
    context: CommitContextIn | None = None


class CommitsIngestIn(BaseModel):
    project: str
    commits: list[CommitIn] = []


class CommitsIngestResultOut(BaseModel):
    accepted: int = 0
    updated: int = 0
    skipped: int = 0
    contexts_added: int = 0


# ---------------------------------------------------------------------------
# Sync cursors
# ---------------------------------------------------------------------------


class CursorIn(BaseModel):
    project: str
    kind: str  # sessions | commits
    device: str = ""  # hostname of the syncing machine
    last_marker: str | None = None


class CursorOut(BaseModel):
    project: str
    kind: str
    last_marker: str | None = None
    last_synced_at: str | None = None
