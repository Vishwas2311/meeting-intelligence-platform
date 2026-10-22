"""Transcripts API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import response_models as contracts
from ..api_common import audit, get_meeting
from ..database import db_session
from ..models import AuditEvent, Segment, SpeakerName
from ..mutations import changed_evidence, lock_meeting
from ..recovery import transcript_segments
from ..schemas import LossEvent, SegmentBatch, SpeakerRename
from ..security import require_actor

router = APIRouter()


@router.post(
    "/api/meetings/{mid}/loss-events",
    response_model=contracts.LossAck,
    response_model_exclude_unset=True,
)
def report_loss(
    mid: str,
    body: LossEvent,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    m = lock_meeting(db, mid, actor)
    from uuid import NAMESPACE_URL, uuid5

    event_id = str(uuid5(NAMESPACE_URL, f"{mid}:{body.id}"))
    existing = db.get(AuditEvent, event_id)
    if not existing:
        db.add(
            AuditEvent(
                id=event_id,
                meeting_id=mid,
                actor=actor,
                action="transcript.loss",
                detail=body.model_dump(),
            )
        )
        changed_evidence(m)
        db.commit()
    return {"id": body.id, "saved": True}


@router.post(
    "/api/meetings/{mid}/segments",
    response_model=contracts.SegmentAck,
    response_model_exclude_unset=True,
)
def segments(
    mid: str,
    body: SegmentBatch,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    m = lock_meeting(db, mid, actor)
    if m.status == "created":
        raise HTTPException(409, "Meeting not in transcription state")
    event_ids = [item.client_event_id for item in body.segments]
    fields = tuple(body.segments[0].model_dump())
    seen = {
        row.client_event_id: {field: getattr(row, field) for field in fields}
        for row in db.scalars(
            select(Segment).where(Segment.meeting_id == mid, Segment.client_event_id.in_(event_ids))
        )
    }
    inserted = 0
    inserted_chars = 0
    for item in body.segments:
        if item.client_event_id in seen:
            if seen[item.client_event_id] != item.model_dump():
                raise HTTPException(409, "Transcript event already exists with different content")
            continue
        if m.status in ("processing", "ready_review"):
            raise HTTPException(
                409, "Minutes are processing or ready. Synchronize transcripts before generation."
            )
        db.add(Segment(meeting_id=mid, **item.model_dump()))
        seen[item.client_event_id] = item.model_dump()
        inserted += 1
        inserted_chars += len(item.text)
    if (
        inserted
        and db.scalar(select(func.count()).select_from(Segment).where(Segment.meeting_id == mid))
        + inserted
        > 10000
    ):
        raise HTTPException(413, "Transcript exceeds 10,000 segments; start a new meeting")
    if inserted:
        size = db.scalar(
            select(func.coalesce(func.sum(func.length(Segment.text)), 0)).where(
                Segment.meeting_id == mid
            )
        )
        if size + inserted_chars > 1_000_000:
            raise HTTPException(
                413, "Transcript exceeds one million characters; start a new meeting"
            )
    if inserted:
        changed_evidence(m)
    db.commit()
    return {"accepted": inserted, "duplicates": len(body.segments) - inserted}


@router.get(
    "/api/meetings/{mid}/transcript",
    response_model=contracts.TranscriptView,
    response_model_exclude_unset=True,
    responses={304: {"description": "Unchanged owner-scoped representation; ETag header"}},
)
def transcript(
    mid: str,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    request: Request = None,
    response: Response = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    after: str | None = None,
):
    meeting = get_meeting(db, mid, actor)
    import hashlib

    etag = (
        '"'
        + hashlib.sha256(
            f"{actor}:{mid}:{meeting.evidence_revision}:{after}:{limit}".encode()
        ).hexdigest()
        + '"'
    )
    if request is not None and request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"}
        )
    if response is not None:
        response.headers["ETag"] = etag
    try:
        ss = transcript_segments(db, mid, limit + 1, after)
    except ValueError:
        raise HTTPException(422, "Invalid transcript cursor") from None
    more = len(ss) > limit
    ss = ss[:limit]
    return {
        "meeting_id": mid,
        "next_cursor": ss[-1].id if more and ss else None,
        "evidence_revision": meeting.evidence_revision,
        "speaker_names": {
            s.speaker_id: s.display_name
            for s in db.scalars(select(SpeakerName).where(SpeakerName.meeting_id == mid))
        },
        "segments": [
            {
                "id": s.id,
                "client_event_id": s.client_event_id,
                "text": s.text,
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "speaker_id": s.speaker_id,
                "source": s.source,
            }
            for s in ss
        ],
    }


@router.patch(
    "/api/meetings/{mid}/speakers",
    response_model=contracts.SpeakerView,
    response_model_exclude_unset=True,
)
def rename_speaker(
    mid: str,
    body: SpeakerRename,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    m = lock_meeting(db, mid, actor)
    if not db.scalar(
        select(Segment.id).where(Segment.meeting_id == mid, Segment.speaker_id == body.speaker_id)
    ):
        raise HTTPException(404, "Speaker has no saved transcript in this meeting yet")
    name = db.get(SpeakerName, (mid, body.speaker_id))
    if name:
        name.display_name = body.display_name
    else:
        db.add(SpeakerName(meeting_id=mid, **body.model_dump()))
    changed_evidence(m)
    audit(db, mid, actor, "speaker.renamed", body.model_dump())
    db.commit()
    return body.model_dump()
