"""SQLAlchemy models for the CC Explorer Cloud backend.

The schema mirrors the shapes produced by ``commit_context.parser.parse_session``
and the commit-context note bundles (see ``commit_context/capture.py``), so
ingestion is a mechanical mapping from parsed transcript/bundle → rows. See
``docs/system-design.md`` §5 for the data-model rationale.

Types are portable: ``Uuid``, ``JSON().with_variant(JSONB, "postgresql")`` and
``DateTime(timezone=True)`` run on both Postgres (prod) and SQLite (dev/tests).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSON that becomes JSONB on Postgres and stays JSON (TEXT) on SQLite.
JSONVariant = JSON().with_variant(JSONB(), "postgresql")

# BIGINT identity on Postgres; SQLite only autoincrements a column declared exactly
# INTEGER, so render Integer there (rowid alias) — same value range semantics.
BigIntPk = BigInteger().with_variant(Integer, "sqlite")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Identity & tenancy
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    teams: Mapped[list[Team]] = relationship(secondary="team_members", back_populates="members")
    devices: Mapped[list[Device]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tokens: Mapped[list[ApiToken]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list[Session]] = relationship(back_populates="user")
    ingest_runs: Mapped[list[IngestRun]] = relationship(back_populates="user")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list[User]] = relationship(secondary="team_members", back_populates="teams")
    projects: Mapped[list[Project]] = relationship(back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    """Membership of a user in a team with a role."""

    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # owner | admin | member
    role: Mapped[str] = mapped_column(String(16), default="member", server_default=text("'member'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    """One git repo shared by a team."""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("team_id", "repo_url", name="uq_projects_team_repo"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    # Route-safe identifier used in URLs, e.g. "cc-session-explorer".
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(240))
    repo_url: Mapped[str] = mapped_column(String(1024))
    default_branch: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    team: Mapped[Team] = relationship(back_populates="projects")
    sessions: Mapped[list[Session]] = relationship(back_populates="project", cascade="all, delete-orphan")
    commits: Mapped[list[Commit]] = relationship(back_populates="project", cascade="all, delete-orphan")
    sync_states: Mapped[list[SyncState]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Device(Base):
    """A dev machine that ingests data (provenance for sessions)."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    hostname: Mapped[str] = mapped_column(String(255))
    os: Mapped[str | None] = mapped_column(String(64))
    claude_version: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="devices")
    sessions: Mapped[list[Session]] = relationship(back_populates="device")
    sync_states: Mapped[list[SyncState]] = relationship(back_populates="device", cascade="all, delete-orphan")


class ApiToken(Base):
    """Long-lived credential for the sync CLI / worker."""

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list] = mapped_column(JSONVariant, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="tokens")


# ---------------------------------------------------------------------------
# Sessions (transcripts)
# ---------------------------------------------------------------------------


class Session(Base):
    """One Claude Code session transcript."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("project_id", "external_id", name="uq_sessions_project_external"),
        Index("ix_sessions_project_started", "project_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    # Local jsonl stem, e.g. "2a7b6f5e-....jsonl" → "2a7b6f5e-…"
    external_id: Mapped[str] = mapped_column(String(120))
    title: Mapped[str | None] = mapped_column(String(300))
    cwd: Mapped[str | None] = mapped_column(String(1024))
    branch: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    models: Mapped[list] = mapped_column(JSONVariant, default=list)
    branches: Mapped[list] = mapped_column(JSONVariant, default=list)
    tool_counts: Mapped[dict] = mapped_column(JSONVariant, default=dict)

    prompts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    thinking: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    responses: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    has_subagents: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    has_tool_results: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Object-storage key for the raw jsonl, when archived.
    raw_key: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project] = relationship(back_populates="sessions")
    user: Mapped[User] = relationship(back_populates="sessions")
    device: Mapped[Device | None] = relationship(back_populates="sessions")
    entries: Mapped[list[SessionEntry]] = relationship(
        back_populates="session", order_by="SessionEntry.seq", cascade="all, delete-orphan"
    )
    subagents: Mapped[list[Subagent]] = relationship(back_populates="session", cascade="all, delete-orphan")
    tool_results: Mapped[list[ToolResult]] = relationship(back_populates="session", cascade="all, delete-orphan")


class SessionEntry(Base):
    """One parsed transcript row; mirrors ``parser.parse_session`` output 1:1."""

    __tablename__ = "session_entries"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_session_entries_session_seq"),
        Index("ix_session_entries_session_kind", "session_id", "kind"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # branch|user|assistant|thinking|tool|system
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(120))  # assistant
    name: Mapped[str | None] = mapped_column(String(80))  # tool name
    arg: Mapped[str | None] = mapped_column(String(255))  # tool arg summary
    params: Mapped[list | None] = mapped_column(JSONVariant)  # tool params [{key, value}]
    result: Mapped[str | None] = mapped_column(Text)  # tool result text
    persisted: Mapped[str | None] = mapped_column(String(255))  # persisted output filename
    meta: Mapped[bool | None] = mapped_column(Boolean)  # user isMeta

    session: Mapped[Session] = relationship(back_populates="entries")


class Subagent(Base):
    """A subagent run inside a session."""

    __tablename__ = "subagents"
    __table_args__ = (UniqueConstraint("session_id", "external_id", name="uq_subagents_session_external"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(120))  # agent-<uuid>
    agent_type: Mapped[str] = mapped_column(String(64), default="?")
    description: Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="subagents")
    entries: Mapped[list[SubagentEntry]] = relationship(
        back_populates="subagent", order_by="SubagentEntry.seq", cascade="all, delete-orphan"
    )


class SubagentEntry(Base):
    """Parsed transcript rows of a subagent; same shape as ``SessionEntry``."""

    __tablename__ = "subagent_entries"
    __table_args__ = (UniqueConstraint("subagent_id", "seq", name="uq_subagent_entries_subagent_seq"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    subagent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subagents.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(80))
    arg: Mapped[str | None] = mapped_column(String(255))
    params: Mapped[list | None] = mapped_column(JSONVariant)
    result: Mapped[str | None] = mapped_column(Text)
    persisted: Mapped[str | None] = mapped_column(String(255))
    meta: Mapped[bool | None] = mapped_column(Boolean)

    subagent: Mapped[Subagent] = relationship(back_populates="entries")


class ToolResult(Base):
    """A persisted tool-result file from a session's ``tool-results/`` sidecar."""

    __tablename__ = "tool_results"
    __table_args__ = (UniqueConstraint("session_id", "name", name="uq_tool_results_session_name"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    content: Mapped[str | None] = mapped_column(Text)  # inline for small files
    storage_key: Mapped[str | None] = mapped_column(String(512))  # object storage for big files
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="tool_results")


# ---------------------------------------------------------------------------
# Git commits & commit context
# ---------------------------------------------------------------------------


class Commit(Base):
    """A git commit of a project (metadata; context lives in ``CommitContext``)."""

    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("project_id", "sha", name="uq_commits_project_sha"),
        Index("ix_commits_project_authored", "project_id", "authored_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    sha: Mapped[str] = mapped_column(String(40))
    short_sha: Mapped[str] = mapped_column(String(12))
    subject: Mapped[str | None] = mapped_column(String(500))
    author_name: Mapped[str | None] = mapped_column(String(255))
    author_email: Mapped[str | None] = mapped_column(String(320))
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refs: Mapped[list] = mapped_column(JSONVariant, default=list)
    parents: Mapped[list] = mapped_column(JSONVariant, default=list)
    has_context: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="commits")
    context: Mapped[CommitContext | None] = relationship(
        back_populates="commit", cascade="all, delete-orphan", uselist=False
    )


class CommitContext(Base):
    """The agent-context bundle (git note body) attached to a commit, 1:1."""

    __tablename__ = "commit_contexts"
    __table_args__ = (UniqueConstraint("commit_id", name="uq_commit_contexts_commit"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    commit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commits.id", ondelete="CASCADE"), index=True
    )
    # Linked to the owning session when it has been ingested and resolvable.
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    branch: Mapped[str | None] = mapped_column(String(255))
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict | None] = mapped_column(JSONVariant)
    entries: Mapped[list | None] = mapped_column(JSONVariant)
    raw: Mapped[str | None] = mapped_column(Text)  # full note bundle JSON
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    commit: Mapped[Commit] = relationship(back_populates="context")
    session: Mapped[Session | None] = relationship()


# ---------------------------------------------------------------------------
# Sync & ingest bookkeeping
# ---------------------------------------------------------------------------


class SyncState(Base):
    """Per-device ingest cursor (server-side mirror of the CLI's local cursor)."""

    __tablename__ = "sync_state"
    __table_args__ = (
        UniqueConstraint("device_id", "project_id", "kind", name="uq_sync_state_device_project_kind"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # sessions | commits
    last_marker: Mapped[str | None] = mapped_column(String(255))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped[Device] = relationship(back_populates="sync_states")
    project: Mapped[Project] = relationship(back_populates="sync_states")


class IngestRun(Base):
    """Audit record of one ingest operation (who, what, from where, outcome)."""

    __tablename__ = "ingest_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))  # sessions | commits | git
    status: Mapped[str] = mapped_column(String(16), default="running", server_default=text("'running'"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sessions_added: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    sessions_updated: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    entries_added: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    subagents_added: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tool_results_added: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    commits_added: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    contexts_added: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User | None] = relationship(back_populates="ingest_runs")
