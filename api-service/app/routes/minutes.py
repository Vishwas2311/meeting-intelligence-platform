"""Minutes API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import response_models as contracts
from ..api_common import audit, collection_cursor, get_meeting, utc_time
from ..database import db_session
from ..models import Job, Meeting, Mom, MomRevision, NotesFinalization, PersonalRecap, Segment, now
from ..mutations import check_revision, lock_meeting
from ..providers import validate_evidence
from ..recovery import has_transcript_loss, require_complete_audio, transcript_segments
from ..schemas import Approval, MomData, MomEdit, RevisionRequest, stored_minutes
from ..security import require_actor

router = APIRouter()


@router.post(
    "/api/meetings/{mid}/mom/generate",
    status_code=202,
    response_model=contracts.QueueAck,
    response_model_exclude_unset=True,
)
def generate(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    # Serialize the read-then-queue decision so simultaneous clicks cannot consume quota twice.
    m = lock_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if db.scalar(
        select(Job.id).where(
            Job.meeting_id == mid,
            Job.kind == "improve_transcript",
            Job.status.in_(["pending", "running"]),
        )
    ):
        raise HTTPException(409, "Wait for transcript improvement before generating MoM.")
    if m.source == "personal-recap":
        recap = db.get(PersonalRecap, mid)
        finalization = db.get(NotesFinalization, mid)
        if finalization and not finalization.accepted:
            raise HTTPException(
                409, "Finish audio finalization and choose the transcript before generating MoM."
            )
        if not recap or not recap.reviewed or not recap.notes_segment_id:
            raise HTTPException(
                409, "Review and save your recap notes before generating a meeting memo."
            )
        if db.scalar(
            select(Job.id).where(
                Job.meeting_id == mid,
                Job.kind == "transcribe_recap",
                Job.status.in_(["pending", "running"]),
            )
        ):
            raise HTTPException(409, "Wait for recap transcription to finish.")
    if mom and mom.status == "approved":
        raise HTTPException(409, "Approved MoM cannot be regenerated; create a revision workflow")
    if m.status not in ("stopped", "ready_review", "processing"):
        raise HTTPException(409, "Stop meeting before MoM generation")
    if (
        m.capture_status == "lost"
        or m.sync_status in ("offline", "failed")
        or has_transcript_loss(db, mid)
    ):
        raise HTTPException(
            409,
            "Capture or transcription is incomplete. Recover and verify the missing transcript before generating minutes.",
        )
    if not db.scalar(select(Segment.id).where(Segment.meeting_id == mid).limit(1)):
        raise HTTPException(
            409,
            "No synchronized transcript. Restore transcription or import the missing transcript first.",
        )
    try:
        require_complete_audio(db, mid)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from None
    pending = db.scalar(
        select(Job).where(
            Job.meeting_id == mid,
            Job.kind == "generate_mom",
            Job.status.in_(["pending", "running"]),
        )
    )
    if pending:
        return {"job_id": pending.id, "status": pending.status}
    from ..limits import admit_job

    admit_job(db, m)
    previous = db.scalar(
        select(Job)
        .where(Job.meeting_id == mid, Job.kind == "generate_mom", Job.status == "failed")
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job = Job(
        meeting_id=mid,
        kind="generate_mom",
        owner_actor=actor,
        input_evidence_revision=m.evidence_revision,
        checkpoint={**(previous.checkpoint or {}), "retry_count": 0} if previous else None,
    )
    db.add(job)
    m.status = "processing"
    m.revision += 1
    db.flush()
    audit(db, mid, actor, "mom.queued", {"job_id": job.id})
    db.commit()
    return {"job_id": job.id, "status": job.status}


@router.get(
    "/api/meetings/{mid}/jobs",
    response_model=list[contracts.JobView],
    response_model_exclude_unset=True,
)
def jobs(
    mid: str,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after: str | None = None,
):
    get_meeting(db, mid, actor)
    query = (
        select(
            Job.id,
            Job.kind,
            Job.status,
            Job.attempts,
            Job.error,
            Job.run_after,
            Job.lease_until,
            Job.completed_sections,
            Job.retry_attempts,
            Job.max_attempts,
        )
        .where(Job.meeting_id == mid)
        .order_by(Job.created_at.desc(), Job.id.desc())
    )
    query = collection_cursor(db, query, Job, mid, after)
    jj = db.execute(query.limit(limit)).all()
    return [
        {
            "id": j.id,
            "kind": j.kind,
            "status": j.status,
            "attempts": j.attempts,
            "error": j.error,
            "run_after": utc_time(j.run_after),
            "lease_until": utc_time(j.lease_until),
            "completed_sections": j.completed_sections,
            "retry_attempts": j.retry_attempts,
            "max_attempts": j.max_attempts,
        }
        for j in jj
    ]


@router.get(
    "/api/meetings/{mid}/jobs/{jid}",
    response_model=contracts.JobView,
    response_model_exclude_unset=True,
)
def job_status(
    mid: str, jid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    get_meeting(db, mid, actor)
    job = (
        db.execute(
            select(
                Job.id,
                Job.kind,
                Job.status,
                Job.attempts,
                Job.error,
                Job.run_after,
                Job.completed_sections,
                Job.completed_at,
            ).where(Job.id == jid, Job.meeting_id == mid)
        )
        .mappings()
        .first()
    )
    if not job:
        raise HTTPException(404, "Job not found")
    return dict(job)


@router.get(
    "/api/meetings/{mid}/mom",
    response_model=contracts.MinutesView,
    response_model_exclude_unset=True,
    responses={304: {"description": "Unchanged owner-scoped representation; ETag header"}},
)
def get_mom(
    mid: str,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    request: Request = None,
    response: Response = None,
):
    get_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if not mom:
        raise HTTPException(404, "MoM not generated")
    import hashlib

    tag = (
        '"'
        + hashlib.sha256(
            f"{actor}:{mid}:{mom.revision}:{mom.status}:{mom.updated_at}".encode()
        ).hexdigest()
        + '"'
    )
    if request is not None and request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers={"ETag": tag})
    if response is not None:
        response.headers["ETag"] = tag
    return {
        "status": mom.status,
        "data": stored_minutes(mom.data),
        "approved_by": mom.approved_by,
        "updated_at": utc_time(mom.updated_at),
        "revision": mom.revision,
        "approved_revision_id": mom.approved_revision_id,
        "approved_at": utc_time(mom.approved_at),
    }


def save_revision(db, mom, actor, provenance=None):
    revision = MomRevision(
        meeting_id=mom.meeting_id,
        data=mom.data,
        status=mom.status,
        actor=actor,
        provenance=provenance or {"schema_version": 1},
        revision_number=mom.revision,
    )
    db.add(revision)
    db.flush()
    return revision.id


@router.get(
    "/api/meetings/{mid}/mom/revisions",
    response_model=list[contracts.RevisionView],
    response_model_exclude_unset=True,
)
def revisions(
    mid: str,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    after: str | None = None,
):
    get_meeting(db, mid, actor)
    query = collection_cursor(
        db, select(MomRevision).where(MomRevision.meeting_id == mid), MomRevision, mid, after
    )
    return [
        {
            "id": r.id,
            "data": stored_minutes(r.data),
            "status": r.status,
            "at": utc_time(r.created_at),
            "provenance": r.provenance,
        }
        for r in db.scalars(
            query.order_by(MomRevision.created_at.desc(), MomRevision.id.desc()).limit(limit)
        )
    ]


@router.post(
    "/api/meetings/{mid}/mom/revise",
    response_model=contracts.MinutesView,
    response_model_exclude_unset=True,
)
def revise(
    mid: str,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    body: RevisionRequest | None = Body(default=None),
):
    m = lock_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if not mom or mom.status != "approved":
        raise HTTPException(409, "Only approved minutes need a new revision")
    db.refresh(mom)
    check_revision(mom, body.expected_revision if body else None)
    save_revision(db, mom, actor)
    mom.data = stored_minutes(mom.data)
    mom.status, mom.approved_by, mom.updated_at = "draft", None, now()
    mom.approved_revision_id = mom.approved_at = None
    mom.revision += 1
    m.revision += 1
    audit(db, mid, actor, "mom.revision_started")
    db.commit()
    return {"status": mom.status, "data": stored_minutes(mom.data), "revision": mom.revision}


@router.patch(
    "/api/meetings/{mid}/mom",
    response_model=contracts.MinutesView,
    response_model_exclude_unset=True,
)
def edit_mom(
    mid: str, body: MomEdit, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    lock_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if not mom:
        raise HTTPException(404, "MoM not generated")
    db.refresh(mom)
    check_revision(mom, getattr(body, "expected_revision", None))
    if mom.status == "approved":
        raise HTTPException(409, "Approved MoM cannot be edited; create a revision workflow")
    if mom.status == "outdated":
        raise HTTPException(
            409,
            "The transcript was recovered. Generate updated minutes before editing or approval.",
        )
    if db.scalar(
        select(Job.id).where(Job.meeting_id == mid, Job.status.in_(["pending", "running"]))
    ):
        raise HTTPException(409, "Wait for generation to finish before editing minutes.")
    try:
        validate_evidence(
            body.minutes() if isinstance(body, MomEdit) else body.model_dump(),
            list(db.scalars(select(Segment.id).where(Segment.meeting_id == mid))),
        )
    except ValueError:
        raise HTTPException(422, "Minutes cite unknown or invalid transcript evidence") from None
    save_revision(db, mom, actor)
    mom.data = body.minutes() if isinstance(body, MomEdit) else body.model_dump()
    mom.revision += 1
    mom.updated_at = now()
    audit(db, mid, actor, "mom.edited")
    db.get(Meeting, mid).revision += 1
    db.commit()
    return {"status": mom.status, "data": stored_minutes(mom.data), "revision": mom.revision}


@router.post(
    "/api/meetings/{mid}/mom/approve",
    response_model=contracts.StatusView,
    response_model_exclude_unset=True,
)
def approve(
    mid: str, body: Approval, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    m = lock_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if not mom:
        raise HTTPException(404, "MoM not generated")
    db.refresh(mom)
    check_revision(mom, body.expected_revision)
    if mom.status == "approved":
        return {"status": "approved"}
    if mom.status == "outdated":
        raise HTTPException(
            409, "Generate updated minutes from the recovered transcript before approval."
        )
    if db.scalar(
        select(Job.id).where(Job.meeting_id == mid, Job.status.in_(["pending", "running"]))
    ):
        raise HTTPException(409, "Wait for generation to finish before approving minutes.")
    if mom.data.get("transcript_gaps"):
        raise HTTPException(
            409,
            "Transcript gaps must be resolved or explicitly acknowledged under a configured policy",
        )
    try:
        require_complete_audio(db, mid)
        parsed = MomData.model_validate(mom.data)
        validate_evidence(parsed.model_dump(), [s.id for s in transcript_segments(db, mid)])
    except (RuntimeError, ValueError):
        raise HTTPException(
            409, "Minutes or recording evidence need correction before approval"
        ) from None
    if (
        m.capture_status == "lost"
        or m.sync_status in ("offline", "failed")
        or has_transcript_loss(db, mid)
    ):
        raise HTTPException(
            409, "Capture or transcription is incomplete. Recover and verify it before approval."
        )
    mom.status = "approved"
    mom.approved_by = actor
    mom.updated_at = now()
    mom.approved_at = now()
    mom.approved_revision_id = save_revision(db, mom, actor)
    m.revision += 1
    audit(db, mid, actor, "mom.approved")
    db.commit()
    return {"status": "approved"}
