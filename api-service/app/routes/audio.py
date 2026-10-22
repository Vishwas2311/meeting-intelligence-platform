"""Audio API endpoints."""

import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import response_models as contracts
from ..api_common import audit, get_meeting
from ..config import Settings, get_settings
from ..database import db_session
from ..models import (
    AudioChunk,
    CaptureManifest,
    Job,
    Meeting,
    Mom,
)
from ..recovery import (
    audio_coverage,
    transcript_segments,
)
from ..schemas import (
    Manifest,
)
from ..security import require_actor

router = APIRouter()


@router.post(
    "/api/meetings/{mid}/manifest",
    response_model=contracts.ManifestAck,
    response_model_exclude_unset=True,
)
def save_manifest(
    mid: str, body: Manifest, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    from ..mutations import changed_evidence, lock_meeting

    m = lock_meeting(db, mid, actor)
    previous = db.get(CaptureManifest, mid)
    data = body.model_dump()
    end = 0
    for pause in body.pauses:
        if pause.start_ms < end or pause.end_ms > body.recorded_through_ms:
            raise HTTPException(422, "Pause intervals overlap or exceed the recording timeline")
        end = pause.end_ms
    if previous and previous.data == data:
        return {"saved": True, "finalized": body.finalized}
    if m.status in ("processing", "ready_review") or (previous and previous.data["finalized"]):
        raise HTTPException(409, "Recording manifest is finalized; original data was kept")
    if previous:
        if (
            body.expected_chunks < previous.data["expected_chunks"]
            or body.recorded_through_ms < previous.data["recorded_through_ms"]
        ):
            raise HTTPException(409, "Recording manifest cannot move backwards")
        previous.data = data
    else:
        db.add(CaptureManifest(meeting_id=mid, data=data))
    if body.recorded_through_ms > get_settings().max_meeting_ms:
        raise HTTPException(422, "Recording exceeds two hours")
    changed_evidence(m)
    db.commit()
    return {"saved": True, "finalized": body.finalized}


@router.post(
    "/api/meetings/{mid}/audio-chunks",
    status_code=201,
    response_model=contracts.ChunkAck,
    response_model_exclude_unset=True,
)
async def chunk(
    mid: str,
    sequence: int = Form(...),
    start_ms: int = Form(...),
    end_ms: int = Form(...),
    sha256: str = Form(...),
    audio: UploadFile = File(...),
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    settings: Settings = Depends(get_settings),
):
    import asyncio
    import io

    stream = getattr(audio, "file", None)
    if stream is None:
        stream = io.BytesIO(await audio.read(settings.max_audio_chunk_mb * 1024**2 + 1))
    return await asyncio.to_thread(
        _commit_chunk,
        mid,
        sequence,
        start_ms,
        end_ms,
        sha256,
        stream,
        audio.content_type,
        db,
        actor,
        settings,
    )


def _commit_chunk(mid, sequence, start_ms, end_ms, sha256, stream, media_type, db, actor, settings):
    from ..audio_store import complete_upload

    return complete_upload(
        lambda: _store_chunk(
            mid, sequence, start_ms, end_ms, sha256, stream, media_type, db, actor, settings
        ),
        db,
        stream,
    )


def _store_chunk(mid, sequence, start_ms, end_ms, sha256, stream, media_type, db, actor, settings):
    import re

    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise HTTPException(422, "Audio checksum must be SHA-256 hex")
    from ..audio_store import (
        abort_reservation,
        finish_reservation,
        reserve,
        storage_key,
        stream_size,
        write_reserved,
    )
    from ..mutations import changed_evidence, lock_meeting

    m = lock_meeting(db, mid, actor)
    if m.status == "created":
        raise HTTPException(409, "Meeting not started")
    if sequence < 0 or start_ms < 0 or end_ms < start_ms or end_ms > settings.max_meeting_ms:
        raise HTTPException(422, "Invalid chunk timeline or recording exceeds two hours")
    size = stream_size(stream)
    if size == 0:
        raise HTTPException(422, "Audio chunk is empty")
    if size > settings.max_audio_chunk_mb * 1024**2:
        raise HTTPException(413, "Audio chunk too large")
    existing = db.scalar(
        select(AudioChunk).where(AudioChunk.meeting_id == mid, AudioChunk.sequence == sequence)
    )
    if existing:
        # A retry still verifies its bytes, rather than trusting a claimed digest.
        actual = hashlib.sha256()
        while block := stream.read(65536):
            actual.update(block)
        if actual.hexdigest() != sha256.lower():
            raise HTTPException(422, "Audio checksum mismatch")
        if existing.sha256 != actual.hexdigest():
            raise HTTPException(409, "Chunk sequence already exists with different content")
        if (existing.start_ms, existing.end_ms) != (start_ms, end_ms):
            raise HTTPException(409, "Chunk sequence already exists with a different timeline")
        return {"id": existing.id, "duplicate": True}
    if m.status in ("processing", "ready_review"):
        raise HTTPException(409, "Synchronize all audio before generating minutes")
    typ = (media_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if typ not in {
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
        "application/octet-stream",
    }:
        raise HTTPException(415, "Unsupported audio format")
    reservation = reserve(db, m, str(sequence), size, settings, expected_sha=sha256.lower())
    try:
        actual, size = write_reserved(reservation, stream, settings.max_audio_chunk_mb * 1024**2)
        if actual != sha256.lower():
            raise HTTPException(422, "Audio checksum mismatch")
        m = lock_meeting(db, mid, actor)
        if m.status in ("processing", "ready_review"):
            raise HTTPException(409, "Meeting changed during upload; recording retained")
        destination = settings.storage_dir / mid / f"{sequence:09d}.bin"
        finish_reservation(db, reservation, destination, actual, size)
        item = AudioChunk(
            meeting_id=mid,
            sequence=sequence,
            start_ms=start_ms,
            end_ms=end_ms,
            sha256=actual,
            path=storage_key(destination, settings),
            size_bytes=size,
            media_type=typ,
        )
        db.add(item)
        changed_evidence(m)
        db.flush()
        audit(db, mid, actor, "audio.chunk_committed", {"sequence": sequence, "size": size})
        db.commit()
        return {"id": item.id, "duplicate": False}
    except BaseException:
        abort_reservation(db, reservation)
        raise


@router.get(
    "/api/meetings/{mid}/audio-chunks",
    response_model=list[contracts.ChunkView],
    response_model_exclude_unset=True,
)
def chunks(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    get_meeting(db, mid, actor)
    cc = db.scalars(
        select(AudioChunk).where(AudioChunk.meeting_id == mid).order_by(AudioChunk.sequence)
    ).all()
    return [
        {
            "sequence": c.sequence,
            "start_ms": c.start_ms,
            "end_ms": c.end_ms,
            "sha256": c.sha256,
            "size_bytes": c.size_bytes,
        }
        for c in cc
    ]


@router.get(
    "/api/meetings/{mid}/coverage",
    response_model=contracts.CoverageView,
    response_model_exclude_unset=True,
)
def coverage(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    get_meeting(db, mid, actor)
    value = audio_coverage(db, mid)
    return {
        "meeting_id": mid,
        **value,
        "final_transcript_segments": len(transcript_segments(db, mid)),
        "warning": "Legacy recording: expected audio count is unknown."
        if not value["manifest"]
        else None,
    }


@router.post(
    "/api/meetings/{mid}/recover",
    status_code=202,
    response_model=contracts.QueueAck,
    response_model_exclude_unset=True,
)
def queue_recovery(
    mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    else:
        db.execute(select(Meeting.id).where(Meeting.id == mid).with_for_update())
    m = get_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if m.status not in ("stopped", "ready_review", "processing") or (
        mom and mom.status == "approved"
    ):
        raise HTTPException(
            409, "Stop the meeting and create a revision of approved minutes before recovery"
        )
    active = db.scalar(
        select(Job).where(Job.meeting_id == mid, Job.status.in_(["pending", "running"]))
    )
    if active:
        if active.kind == "recover_transcript":
            return {"job_id": active.id, "status": active.status}
        raise HTTPException(409, "Wait for the current job before recovery")
    if not get_settings().azure_speech_fast_endpoint:
        raise HTTPException(503, "Fast transcription endpoint is not configured")
    if not audio_coverage(db, mid)["audio_capture_claimed_complete"]:
        raise HTTPException(
            409, "A complete finalized recording is required for automatic recovery"
        )
    from ..limits import admit_job

    admit_job(db, m)
    job = Job(
        meeting_id=mid,
        kind="recover_transcript",
        owner_actor=actor,
        input_evidence_revision=m.evidence_revision,
    )
    db.add(job)
    db.flush()
    audit(db, mid, actor, "transcript.recovery_queued")
    db.commit()
    return {"job_id": job.id, "status": job.status}
