"""Meetings API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .. import response_models as contracts
from ..api_common import audit, collection_cursor, get_meeting, utc_time, view
from ..database import db_session
from ..models import AuditEvent, Meeting, Mom, now
from ..mutations import lock_meeting
from ..schemas import CaptureUpdate, MeetingCreate, stored_minutes
from ..security import require_actor
from .minutes import jobs
from .transcripts import transcript

router = APIRouter()


@router.post(
    "/api/meetings",
    status_code=201,
    response_model=contracts.MeetingView,
    response_model_exclude_unset=True,
)
def create(
    body: MeetingCreate, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    if not body.consent_confirmed:
        raise HTTPException(
            422,
            "Recording authorization/notice must be confirmed before creating a recording session",
        )
    m = Meeting(
        title=body.title,
        language=body.language,
        source=body.source,
        consent_confirmed=True,
        created_by=actor,
    )
    db.add(m)
    db.flush()
    audit(db, m.id, actor, "meeting.created")
    db.commit()
    return view(m)


@router.get(
    "/api/meetings", response_model=list[contracts.MeetingView], response_model_exclude_unset=True
)
def list_meetings(
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    q: str = Query("", max_length=255),
    after: str | None = None,
):
    query = select(Meeting).where(Meeting.created_by == actor)
    if after:
        anchor = db.get(Meeting, after)
        if not anchor or anchor.created_by != actor:
            raise HTTPException(422, "Invalid history cursor")
        query = query.where(
            or_(
                Meeting.created_at < anchor.created_at,
                and_(Meeting.created_at == anchor.created_at, Meeting.id < anchor.id),
            )
        )
    return [
        view(m)
        for m in db.scalars(
            query.where(Meeting.title.ilike(f"%{q}%", escape="\\"))
            .order_by(Meeting.created_at.desc(), Meeting.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ]


@router.get(
    "/api/meetings/{mid}", response_model=contracts.MeetingView, response_model_exclude_unset=True
)
def details(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    return view(get_meeting(db, mid, actor))


@router.post(
    "/api/meetings/{mid}/start",
    response_model=contracts.MeetingView,
    response_model_exclude_unset=True,
)
def start(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    m = lock_meeting(db, mid, actor)
    if m.status != "created":
        raise HTTPException(409, "Meeting can only start from created status")
    # This starts the backend session, NOT the microphone. Client reports actual capture separately.
    m.status = "starting"
    m.started_at = now()
    m.revision += 1
    audit(db, mid, actor, "meeting.start_requested")
    db.commit()
    return view(m)


@router.post(
    "/api/meetings/{mid}/capture-status",
    response_model=contracts.MeetingView,
    response_model_exclude_unset=True,
)
def update_capture(
    mid: str,
    body: CaptureUpdate,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    m = lock_meeting(db, mid, actor)
    if m.status not in ("starting", "recording", "paused", "interrupted"):
        raise HTTPException(409, "Meeting not capturing")
    if m.status == "starting" and body.capture_status == "active":
        m.status = "recording"
    elif body.capture_status == "paused" and m.status == "recording":
        m.status = "paused"
    elif body.capture_status == "lost" and m.status in ("recording", "paused"):
        m.status = "interrupted"
    m.capture_status = body.capture_status
    m.stt_status = body.stt_status
    m.sync_status = body.sync_status
    m.revision += 1
    audit(db, mid, actor, "capture.status", body.model_dump())
    db.commit()
    return view(m)


@router.post(
    "/api/meetings/{mid}/pause",
    response_model=contracts.MeetingView,
    response_model_exclude_unset=True,
)
def pause(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    m = lock_meeting(db, mid, actor)
    if m.status != "recording":
        raise HTTPException(409, "Only recording meeting can be paused")
    m.status = "paused"
    m.capture_status = "paused"
    m.revision += 1
    audit(db, mid, actor, "meeting.paused")
    db.commit()
    return view(m)


@router.post(
    "/api/meetings/{mid}/resume",
    response_model=contracts.MeetingView,
    response_model_exclude_unset=True,
)
def resume(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    m = lock_meeting(db, mid, actor)
    if m.status not in ("paused", "interrupted"):
        raise HTTPException(409, "Meeting is not paused/interrupted")
    m.status = "starting"
    m.capture_status = "unknown"
    m.revision += 1
    audit(db, mid, actor, "meeting.resume_requested")
    db.commit()
    return view(m)


@router.post(
    "/api/meetings/{mid}/stop",
    response_model=contracts.MeetingView,
    response_model_exclude_unset=True,
)
def stop(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    m = lock_meeting(db, mid, actor)
    if m.status in ("stopped", "processing", "ready_review"):
        return view(m)
    if m.status not in ("starting", "recording", "paused", "interrupted"):
        raise HTTPException(409, "Meeting was not started")
    m.status = "stopped"
    m.capture_status = "lost" if m.capture_status == "lost" else "stopped"
    m.ended_at = now()
    m.revision += 1
    audit(db, mid, actor, "meeting.stopped")
    db.commit()
    return view(m)


@router.get(
    "/api/meetings/{mid}/audit",
    response_model=list[contracts.AuditView],
    response_model_exclude_unset=True,
)
def audits(
    mid: str,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after: str | None = None,
):
    get_meeting(db, mid, actor)
    query = collection_cursor(
        db,
        select(AuditEvent).where(AuditEvent.meeting_id == mid),
        AuditEvent,
        mid,
        after,
        descending=False,
    )
    events = db.scalars(query.order_by(AuditEvent.created_at, AuditEvent.id).limit(limit)).all()
    return [
        {
            "id": e.id,
            "action": e.action,
            "at": utc_time(e.created_at),
            "actor": e.actor,
            "detail": e.detail,
        }
        for e in events
    ]


@router.get(
    "/api/meetings/{mid}/snapshot",
    response_model=contracts.MeetingSnapshot,
    response_model_exclude_unset=True,
)
def meeting_snapshot(
    mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    m = get_meeting(db, mid, actor)
    latest_jobs = jobs(mid, db, actor)
    mom = db.get(Mom, mid)
    db.refresh(m)
    saved_transcript = transcript(mid, db, actor)
    return {
        "id": mid,
        "meeting": view(m),
        "segments": saved_transcript["segments"],
        "speakerNames": saved_transcript["speaker_names"],
        "transcript_next_cursor": saved_transcript["next_cursor"],
        "minutes": {
            "status": mom.status,
            "data": stored_minutes(mom.data),
            "approved_by": mom.approved_by,
            "revision": mom.revision,
            "approved_revision_id": mom.approved_revision_id,
            "approved_at": utc_time(mom.approved_at),
        }
        if mom
        else None,
        "jobs": latest_jobs,
    }
