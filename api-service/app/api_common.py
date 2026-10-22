"""Shared ownership, audit and response helpers for API routes."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .models import (
    AuditEvent,
    Meeting,
)


def audit(db: Session, mid: str, actor: str, action: str, detail: dict | None = None) -> None:
    from .observability import current_request

    db.add(
        AuditEvent(
            meeting_id=mid,
            actor=actor,
            action=action,
            detail={**(detail or {}), "request_id": current_request.get()},
        )
    )


def get_meeting(db: Session, mid: str, actor: str) -> Meeting:
    m = db.get(Meeting, mid)
    if not m:
        raise HTTPException(404, "Meeting not found")
    if m.created_by != actor:
        raise HTTPException(403, "Meeting access denied")
    return m


def utc_time(value: datetime | None) -> datetime | None:
    # SQLite drops timezone metadata from our UTC timestamps when reading them back.
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def collection_cursor(db, query, model, mid, after, descending=True):
    if not after:
        return query
    anchor = db.get(model, after)
    if not anchor or anchor.meeting_id != mid:
        raise HTTPException(422, "Invalid collection cursor")
    earlier = (
        model.created_at < anchor.created_at if descending else model.created_at > anchor.created_at
    )
    tie = model.id < anchor.id if descending else model.id > anchor.id
    return query.where(or_(earlier, and_(model.created_at == anchor.created_at, tie)))


def view(m: Meeting) -> dict:
    return {
        "id": m.id,
        "title": m.title,
        "status": m.status,
        "language": m.language,
        "source": m.source,
        "consent_confirmed": m.consent_confirmed,
        "capture_status": m.capture_status,
        "stt_status": m.stt_status,
        "sync_status": m.sync_status,
        "created_at": utc_time(m.created_at),
        "started_at": utc_time(m.started_at),
        "ended_at": utc_time(m.ended_at),
        "revision": m.revision,
        "evidence_revision": m.evidence_revision,
    }
