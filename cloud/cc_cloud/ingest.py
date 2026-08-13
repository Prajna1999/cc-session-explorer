"""Ingest service: map parsed transcript/bundle payloads onto the SQL models.

Kept separate from the HTTP layer so the sync CLI, the git webhook worker and the
API all share one code path. Idempotency comes from the unique constraints in
``models.py`` (sessions by (project, external_id), commits by (project, sha), etc.)
plus delete-and-reinsert of a session's children, which makes a re-sync converge to
exactly the source transcript.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from commit_context.parser import parse_ts

from .models import (
    Commit,
    CommitContext,
    Device,
    IngestRun,
    Project,
    Session as SessionRow,
    SessionEntry,
    Subagent,
    SubagentEntry,
    ToolResult,
    User,
)
from .schemas import (
    CommitIn,
    CommitsIngestIn,
    CommitsIngestResultOut,
    DeviceIn,
    IngestResultOut,
    SessionIn,
    SessionsIngestIn,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


def get_or_create_device(db: Session, user: User, device_in: DeviceIn) -> Device:
    hostname = device_in.hostname or "unknown"
    device = (
        db.query(Device)
        .filter(Device.user_id == user.id, Device.hostname == hostname)
        .first()
    )
    if device is None:
        device = Device(user_id=user.id, hostname=hostname)
        db.add(device)
    if device_in.os:
        device.os = device_in.os
    if device_in.claude_version:
        device.claude_version = device_in.claude_version
    device.last_seen_at = datetime.now(UTC)
    db.flush()
    return device


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _entry_row(session_id, e) -> SessionEntry:
    return SessionEntry(
        session_id=session_id,
        seq=e.seq,
        kind=e.kind,
        ts=parse_ts(e.ts),
        text=e.text,
        model=e.model,
        name=e.name,
        arg=e.arg,
        params=e.params,
        result=e.result,
        persisted=e.persisted,
        meta=e.meta,
    )


def upsert_session(
    db: Session, project: Project, user: User, device: Device | None, payload: SessionIn
) -> tuple[SessionRow, bool, IngestResultOut]:
    """Insert or update one session; children are delete-and-reinserted."""
    row = (
        db.query(SessionRow)
        .filter(SessionRow.project_id == project.id, SessionRow.external_id == payload.external_id)
        .first()
    )
    created = row is None
    if row is None:
        row = SessionRow(project_id=project.id, user_id=user.id, external_id=payload.external_id)
        db.add(row)
        db.flush()

    row.user_id = user.id
    if device is not None:
        row.device_id = device.id
    row.title = payload.title
    row.cwd = payload.cwd
    row.branch = payload.branch
    row.version = payload.version
    row.started_at = parse_ts(payload.started_at)
    row.ended_at = parse_ts(payload.ended_at)
    row.models = payload.models
    row.branches = payload.branches
    row.tool_counts = payload.tool_counts
    row.prompts = payload.prompts
    row.tool_calls = payload.tool_calls
    row.thinking = payload.thinking
    row.responses = payload.responses
    row.size_bytes = payload.size_bytes
    row.has_subagents = payload.has_subagents or bool(payload.subagents)
    row.has_tool_results = payload.has_tool_results or bool(payload.tool_results)
    db.flush()

    # Children: delete-and-reinsert for exact convergence with the source file.
    db.query(SessionEntry).filter(SessionEntry.session_id == row.id).delete()
    entries_added = len(payload.entries)
    for e in payload.entries:
        db.add(_entry_row(row.id, e))

    db.query(Subagent).filter(Subagent.session_id == row.id).delete()
    subagents_added = 0
    for s in payload.subagents:
        sub = Subagent(
            session_id=row.id,
            external_id=s.external_id,
            agent_type=s.agent_type,
            description=s.description,
            size_bytes=s.size_bytes,
        )
        db.add(sub)
        db.flush()
        subagents_added += len(s.entries)
        for e in s.entries:
            db.add(
                SubagentEntry(
                    subagent_id=sub.id,
                    seq=e.seq,
                    kind=e.kind,
                    ts=parse_ts(e.ts),
                    text=e.text,
                    model=e.model,
                    name=e.name,
                    arg=e.arg,
                    params=e.params,
                    result=e.result,
                    persisted=e.persisted,
                    meta=e.meta,
                )
            )

    db.query(ToolResult).filter(ToolResult.session_id == row.id).delete()
    tool_results_added = len(payload.tool_results)
    for t in payload.tool_results:
        db.add(ToolResult(session_id=row.id, name=t.name, size_bytes=t.size_bytes, content=t.content))

    result = IngestResultOut(
        accepted=1 if created else 0,
        updated=0 if created else 1,
        skipped=0,
        entries_added=entries_added,
        subagents_added=subagents_added,
        tool_results_added=tool_results_added,
    )
    return row, created, result


def ingest_sessions(
    db: Session, project: Project, user: User, payload: SessionsIngestIn
) -> IngestResultOut:
    device = get_or_create_device(db, user, payload.device)
    run = IngestRun(user_id=user.id, device_id=device.id, project_id=project.id, kind="sessions")
    db.add(run)
    db.flush()

    total = IngestResultOut()
    for s in payload.sessions:
        _, created, result = upsert_session(db, project, user, device, s)
        total.accepted += result.accepted
        total.updated += result.updated
        total.entries_added += result.entries_added
        total.subagents_added += result.subagents_added
        total.tool_results_added += result.tool_results_added

    run.status = "done"
    run.finished_at = datetime.now(UTC)
    run.sessions_added = total.accepted
    run.sessions_updated = total.updated
    run.entries_added = total.entries_added
    run.subagents_added = total.subagents_added
    run.tool_results_added = total.tool_results_added
    db.commit()
    return total


# ---------------------------------------------------------------------------
# Commits + commit context
# ---------------------------------------------------------------------------


def _link_context_session(db: Session, project: Project, ctx: CommitContext, session_id: str | None) -> None:
    if not session_id:
        return
    linked = (
        db.query(SessionRow)
        .filter(SessionRow.project_id == project.id, SessionRow.external_id == session_id)
        .first()
    )
    if linked is not None:
        ctx.session_id = linked.id


def upsert_commit(db: Session, project: Project, payload: CommitIn) -> tuple[Commit, bool, int]:
    """Insert or update one commit (+ optional context bundle). Returns (row, created, contexts_added)."""
    row = (
        db.query(Commit)
        .filter(Commit.project_id == project.id, Commit.sha == payload.sha)
        .first()
    )
    created = row is None
    if row is None:
        row = Commit(project_id=project.id, sha=payload.sha, short_sha=payload.sha[:12])
        db.add(row)
        db.flush()

    row.subject = payload.subject
    row.author_name = payload.author_name
    row.author_email = payload.author_email
    row.authored_at = parse_ts(payload.authored_at)
    row.refs = payload.refs
    row.parents = payload.parents

    contexts_added = 0
    if payload.context is not None:
        ctx = row.context
        if ctx is None:
            ctx = CommitContext(commit_id=row.id)
            db.add(ctx)
            contexts_added = 1
        ctx.branch = payload.context.branch
        ctx.schema_version = payload.context.schema_version
        ctx.captured_at = parse_ts(payload.context.captured_at)
        ctx.meta = payload.context.meta
        ctx.entries = [e.model_dump() for e in payload.context.entries]
        ctx.raw = payload.context.model_dump_json()
        _link_context_session(db, project, ctx, payload.context.session_id)
        row.has_context = True
    else:
        row.has_context = bool(row.context)
    return row, created, contexts_added


def ingest_commits(db: Session, project: Project, user: User | None, payload: CommitsIngestIn) -> CommitsIngestResultOut:
    run = IngestRun(user_id=user.id if user else None, project_id=project.id, kind="commits")
    db.add(run)
    db.flush()

    total = CommitsIngestResultOut()
    for c in payload.commits:
        _, created, contexts_added = upsert_commit(db, project, c)
        if created:
            total.accepted += 1
        else:
            total.updated += 1
        total.contexts_added += contexts_added

    run.status = "done"
    run.finished_at = datetime.now(UTC)
    run.commits_added = total.accepted
    run.contexts_added = total.contexts_added
    db.commit()
    return total


# ---------------------------------------------------------------------------
# Cursors (server-side mirror of the CLI's local cursor)
# ---------------------------------------------------------------------------


def get_cursor(db: Session, project: Project, device: Device, kind: str) -> dict | None:
    from .models import SyncState

    state = (
        db.query(SyncState)
        .filter(
            SyncState.device_id == device.id,
            SyncState.project_id == project.id,
            SyncState.kind == kind,
        )
        .first()
    )
    if state is None:
        return None
    return {
        "last_marker": state.last_marker,
        "last_synced_at": state.last_synced_at.isoformat() if state.last_synced_at else None,
    }


def set_cursor(db: Session, project: Project, device: Device, kind: str, marker: str | None) -> dict:
    from .models import SyncState

    state = (
        db.query(SyncState)
        .filter(
            SyncState.device_id == device.id,
            SyncState.project_id == project.id,
            SyncState.kind == kind,
        )
        .first()
    )
    now = datetime.now(UTC)
    if state is None:
        state = SyncState(device_id=device.id, project_id=project.id, kind=kind)
        db.add(state)
    state.last_marker = marker
    state.last_synced_at = now
    db.commit()
    return {"last_marker": marker, "last_synced_at": now.isoformat()}


# ---------------------------------------------------------------------------
# Bundle parsing (git-notes worker path)
# ---------------------------------------------------------------------------


def commit_from_bundle(db: Session, project: Project, user: User, bundle: dict) -> CommitIn:
    """Convert a raw git-note bundle (capture.py shape) into a CommitIn payload.

    Used by the webhook worker: the bundle's ``commit`` sha is guaranteed to exist in
    the repo, so commit metadata can be filled from the bundle itself where possible.
    """
    entries = []
    for i, e in enumerate(bundle.get("entries", [])):
        entries.append({"seq": i, **e})
    meta = bundle.get("meta") or {}
    return CommitIn(
        sha=bundle["commit"],
        subject=meta.get("subject"),
        author_name=meta.get("author_name"),
        author_email=meta.get("author_email"),
        authored_at=meta.get("authored_at"),
        context={
            "schema_version": bundle.get("schema_version", 1),
            "session_id": bundle.get("session_id"),
            "branch": bundle.get("branch"),
            "captured_at": bundle.get("captured_at"),
            "entries": entries,
            "meta": meta,
        },
    )


def bundle_json_to_dict(raw: str) -> dict:
    return json.loads(raw)
