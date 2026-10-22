"""Optional speaker-aware audio candidates. No text or name changes before explicit adoption."""

import asyncio
import math
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import response_models as contracts
from .config import get_settings
from .database import db_session
from .models import AudioChunk, AuditEvent, Job, Meeting, Mom, Segment, SpeakerName
from .providers import fast_transcribe_file
from .query_helpers import ids_in
from .recovery import (
    decode_audio,
    fingerprint,
    loss_ids,
    require_complete_audio,
    transcript_segments,
    wall_time,
)
from .security import require_actor

router = APIRouter(prefix="/api/meetings", tags=["Transcript improvement"])


def owned(db, mid, actor):
    meeting = db.get(Meeting, mid)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if meeting.created_by != actor:
        raise HTTPException(403, "Meeting access denied")
    return meeting


def lock(db, mid):
    from .mutations import lock_meeting

    return lock_meeting(db, mid)


def editable(db, meeting):
    mom = db.get(Mom, meeting.id)
    if (
        meeting.source == "personal-recap"
        or meeting.status not in ("stopped", "ready_review")
        or (mom and mom.status == "approved")
    ):
        raise HTTPException(
            409,
            "Stop the meeting and create a new minutes revision before improving an approved transcript.",
        )
    if db.scalar(
        select(Job.id).where(Job.meeting_id == meeting.id, Job.status.in_(["pending", "running"]))
    ):
        raise HTTPException(409, "Wait for the current job to finish.")


def latest_candidate(db, mid):
    return db.scalar(
        select(AuditEvent)
        .where(AuditEvent.meeting_id == mid, AuditEvent.action == "transcript.improvement_ready")
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )


def suggestions(original, candidate):
    """Timing overlap proposes identity only; the user must confirm every assignment."""
    scores = {}
    for item in candidate:
        if not item.speaker_id:
            continue
        votes = scores.setdefault(item.speaker_id, {})
        for live in original:
            if live.speaker_id and live.speaker_id.lower() not in {"unknown", "unidentified"}:
                overlap = max(0, min(item.end_ms, live.end_ms) - max(item.start_ms, live.start_ms))
                votes[live.speaker_id] = votes.get(live.speaker_id, 0) + overlap
    result = {}
    for speaker, votes in scores.items():
        ordered = sorted(votes.items(), key=lambda item: item[1], reverse=True)
        total = sum(votes.values())
        if ordered and total >= 1000 and ordered[0][1] / total >= 0.8:
            result[speaker] = ordered[0][0]
    # Two recovered voices pointing to one old voice is ambiguous, not an automatic merge.
    return {
        speaker: target
        for speaker, target in result.items()
        if list(result.values()).count(target) == 1
    }


def segment_view(item):
    return {
        key: getattr(item, key)
        for key in ("id", "client_event_id", "text", "start_ms", "end_ms", "speaker_id", "source")
    }


@router.post(
    "/{mid}/improve",
    status_code=202,
    response_model=contracts.QueueAck,
    response_model_exclude_unset=True,
)
def queue(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    lock(db, mid)
    meeting = owned(db, mid, actor)
    active = db.scalar(
        select(Job).where(
            Job.meeting_id == mid,
            Job.kind == "improve_transcript",
            Job.status.in_(["pending", "running"]),
        )
    )
    if active:
        return {"job_id": active.id, "status": active.status}
    editable(db, meeting)
    if not get_settings().azure_speech_fast_endpoint:
        raise HTTPException(503, "Azure Fast Transcription is not configured.")
    try:
        complete = require_complete_audio(db, mid)["audio_capture_claimed_complete"]
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from None
    if not complete:
        raise HTTPException(409, "Synchronize a complete finalized recording first.")
    previous = latest_candidate(db, mid)
    base = [item.id for item in transcript_segments(db, mid)]
    if (
        previous
        and previous.detail["fingerprint"] == fingerprint(db, mid)
        and previous.detail["base_ids"] == base
    ):
        return {"job_id": previous.detail["job_id"], "status": "completed"}
    from .limits import admit_job

    admit_job(db, meeting)
    job = Job(
        meeting_id=mid,
        kind="improve_transcript",
        owner_actor=actor,
        input_evidence_revision=meeting.evidence_revision,
    )
    db.add(job)
    db.commit()
    return {"job_id": job.id, "status": job.status}


@router.get(
    "/{mid}/improvement",
    response_model=contracts.ImprovementView,
    response_model_exclude_unset=True,
)
def details(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    owned(db, mid, actor)
    job = db.scalar(
        select(Job)
        .where(Job.meeting_id == mid, Job.kind == "improve_transcript")
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    candidate = latest_candidate(db, mid)
    active = transcript_segments(db, mid)
    names = {
        item.speaker_id: item.display_name
        for item in db.scalars(select(SpeakerName).where(SpeakerName.meeting_id == mid))
    }
    segments = (
        db.scalars(
            select(Segment).where(
                Segment.meeting_id == mid, ids_in(Segment.id, candidate.detail["segment_ids"])
            )
        ).all()
        if candidate
        else []
    )
    resolution = (
        any(
            detail.get("candidate_id") == candidate.id
            for detail in db.scalars(
                select(AuditEvent.detail)
                .where(
                    AuditEvent.meeting_id == mid,
                    AuditEvent.action == "transcript.improvement_resolved",
                )
                .order_by(AuditEvent.created_at.desc())
                .limit(1000)
            )
        )
        if candidate
        else False
    )
    return {
        "status": job.status if job else "none",
        "error": job.error if job else None,
        "candidate_id": candidate.id if candidate else None,
        "resolved": bool(resolution),
        "stale": bool(
            candidate
            and (
                candidate.detail["fingerprint"] != fingerprint(db, mid)
                or candidate.detail["base_ids"] != [item.id for item in active]
            )
        ),
        "suggestions": candidate.detail["suggestions"] if candidate else {},
        "original": [segment_view(item) for item in active],
        "candidate": [segment_view(item) for item in sorted(segments, key=lambda s: s.start_ms)],
        "speaker_names": names,
    }


class Adoption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(max_length=36)
    keep_original: bool = False
    speaker_mapping: dict[str, str | None] = Field(default_factory=dict, max_length=100)


@router.post(
    "/{mid}/improvement/apply",
    response_model=contracts.StatusView,
    response_model_exclude_unset=True,
)
def adopt(
    mid: str, body: Adoption, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    lock(db, mid)
    meeting = owned(db, mid, actor)
    editable(db, meeting)
    candidate = latest_candidate(db, mid)
    if not candidate or candidate.id != body.candidate_id:
        raise HTTPException(409, "The candidate changed. Reload the comparison.")
    live = transcript_segments(db, mid)
    if candidate.detail["fingerprint"] != fingerprint(db, mid) or candidate.detail["base_ids"] != [
        item.id for item in live
    ]:
        raise HTTPException(409, "Recording or transcript changed. Create a fresh comparison.")
    if not body.keep_original:
        segments = db.scalars(
            select(Segment).where(
                Segment.meeting_id == mid, ids_in(Segment.id, candidate.detail["segment_ids"])
            )
        ).all()
        speakers = {item.speaker_id for item in segments if item.speaker_id}
        allowed = {item.speaker_id for item in live if item.speaker_id}
        if set(body.speaker_mapping) != speakers or any(
            target is not None and target not in allowed for target in body.speaker_mapping.values()
        ):
            raise HTTPException(
                422, "Confirm a mapping or keep a new identity for every recovered speaker."
            )
        adopted = []
        for item in segments:
            mapped = body.speaker_mapping.get(item.speaker_id) or item.speaker_id
            adopted.append(
                Segment(
                    meeting_id=mid,
                    client_event_id=f"adopt:{uuid4()}",
                    text=item.text,
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    speaker_id=mapped,
                    source="recovered",
                )
            )
        db.add_all(adopted)
        db.flush()
        db.add(
            AuditEvent(
                meeting_id=mid,
                actor=actor,
                action="transcript.recovered",
                detail={
                    "loss_ids": candidate.detail["loss_ids"],
                    "fingerprint": candidate.detail["fingerprint"],
                    "segment_ids": [s.id for s in adopted],
                },
            )
        )
        mom = db.get(Mom, mid)
        if mom:
            mom.status = "outdated"
        from .mutations import changed_evidence

        changed_evidence(meeting)
    db.add(
        AuditEvent(
            meeting_id=mid,
            actor=actor,
            action="transcript.improvement_resolved",
            detail={
                "candidate_id": candidate.id,
                "kept_original": body.keep_original,
                "speaker_mapping": body.speaker_mapping,
            },
        )
    )
    db.commit()
    return {"status": "kept_original" if body.keep_original else "applied"}


async def improve_transcript(mid, jid, claim):
    from .database import SessionLocal

    with SessionLocal() as db:
        meeting = db.get(Meeting, mid)
        evidence_revision = meeting.evidence_revision if meeting else None
        mom = db.get(Mom, mid)
        if (
            not meeting
            or meeting.status not in ("stopped", "ready_review")
            or (mom and mom.status == "approved")
        ):
            raise RuntimeError("Stop recording and unlock approved minutes before improving.")
        coverage = require_complete_audio(db, mid)
        if not coverage["audio_capture_claimed_complete"]:
            raise RuntimeError("A complete finalized recording is required.")
        chunks = db.scalars(
            select(AudioChunk).where(AudioChunk.meeting_id == mid).order_by(AudioChunk.sequence)
        ).all()
        original = transcript_segments(db, mid)
        base = [item.id for item in original]
        before, losses, language = fingerprint(db, mid), sorted(loss_ids(db, mid)), meeting.language
        manifest = coverage["manifest"]
    audio, duration = await asyncio.to_thread(decode_audio, chunks, manifest)
    response = await fast_transcribe_file(
        get_settings(), audio, "meeting.wav", "audio/wav", language, diarization=True
    )
    candidate = []
    for phrase in response.get("phrases", []):
        text = phrase.get("text", "").strip()
        if not text:
            continue
        start, length = phrase.get("offsetMilliseconds"), phrase.get("durationMilliseconds")
        if (
            any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in (start, length)
            )
            or not 0 <= start <= start + length <= duration + 1000
        ):
            raise RuntimeError("Invalid phrase timing; original transcript retained.")
        speaker = phrase.get("speaker")
        if speaker is not None and (
            isinstance(speaker, bool) or not isinstance(speaker, int) or not 0 <= speaker < 100
        ):
            raise RuntimeError("Invalid speaker identifier; original transcript retained.")
        candidate.append(
            Segment(
                meeting_id=mid,
                client_event_id=f"improve:{jid}:{len(candidate)}",
                text=text,
                start_ms=wall_time(start, manifest["pauses"]),
                end_ms=wall_time(start + length, manifest["pauses"]),
                speaker_id=f"audio:{jid}:{speaker}" if speaker is not None else None,
                source="improvement",
            )
        )
    if not candidate:
        raise RuntimeError("No timed speech returned. Original transcript retained.")
    with SessionLocal() as db:
        from .mutations import finish_job, lock_meeting

        locked_meeting = lock_meeting(db, mid)
        if locked_meeting.evidence_revision != evidence_revision:
            raise RuntimeError("Meeting evidence changed during processing; original data retained")
        job = db.get(Job, jid)
        mom = db.get(Mom, mid)
        if (
            not job
            or job.claim_token != claim
            or job.status != "running"
            or fingerprint(db, mid) != before
            or [s.id for s in transcript_segments(db, mid)] != base
            or (mom and mom.status == "approved")
        ):
            raise RuntimeError("Meeting changed. Original transcript retained.")
        db.add_all(candidate)
        db.flush()
        db.add(
            AuditEvent(
                meeting_id=mid,
                actor="system",
                action="transcript.improvement_ready",
                detail={
                    "job_id": jid,
                    "base_ids": base,
                    "fingerprint": before,
                    "loss_ids": losses,
                    "segment_ids": [s.id for s in candidate],
                    "suggestions": suggestions(original, candidate),
                },
            )
        )
        locked_meeting.revision += 1
        finish_job(db, jid, claim)
        db.commit()
