import hashlib
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import db_session, initialize_database
from .models import (
    AudioChunk,
    AuditEvent,
    CaptureManifest,
    Job,
    Meeting,
    Mom,
    MomRevision,
    Segment,
    SpeakerName,
    WorkerState,
    now,
)
from .providers import azure_speech_token, validate_evidence
from .recovery import (
    audio_coverage,
    has_transcript_loss,
    require_complete_audio,
    transcript_segments,
)
from .schemas import (
    Approval,
    CaptureUpdate,
    LossEvent,
    Manifest,
    MeetingCreate,
    MomData,
    SegmentBatch,
    SpeakerRename,
    stored_minutes,
)
from .security import require_actor
from .speech_tokens import SpeechTokenCache
from .storage import storage_status


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().storage_dir.mkdir(parents=True, exist_ok=True)
    initialize_database()
    app.state.speech_tokens = SpeechTokenCache(lambda settings: azure_speech_token(settings))
    yield


app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)


def audit(db: Session, mid: str, actor: str, action: str, detail: dict | None = None) -> None:
    db.add(AuditEvent(meeting_id=mid, actor=actor, action=action, detail=detail or {}))


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
    }


@app.get("/api/health")
def health():
    return {"status": "alive"}


@app.get("/api/ready")
def ready(db: Session = Depends(db_session)):
    db.execute(select(Meeting).limit(1)).all()
    state = db.get(WorkerState, "local")
    worker_ready = bool(state and (now() - state.seen_at.replace(tzinfo=UTC)).total_seconds() < 45)
    return {"status": "ready", "database": True, "worker": worker_ready}


@app.get("/api/storage", dependencies=[Depends(require_actor)])
def storage():
    return storage_status()


@app.post("/api/storage/backup", dependencies=[Depends(require_actor)])
def download_backup():
    import tempfile
    from pathlib import Path
    from uuid import uuid4

    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    from .storage import backup

    target = Path(tempfile.gettempdir()) / f"meeting-backup-{uuid4()}.zip"
    try:
        backup(target)
    except RuntimeError as error:
        target.unlink(missing_ok=True)
        raise HTTPException(409, str(error)) from None
    return FileResponse(
        target,
        filename="meeting-intelligence-backup.zip",
        media_type="application/zip",
        background=BackgroundTask(target.unlink, missing_ok=True),
    )


@app.post("/api/meetings", status_code=201)
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


@app.get("/api/meetings")
def list_meetings(
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    q: str = Query("", max_length=255),
):
    return [
        view(m)
        for m in db.scalars(
            select(Meeting)
            .where(Meeting.created_by == actor)
            .where(Meeting.title.ilike(f"%{q}%", escape="\\"))
            .order_by(Meeting.created_at.desc(), Meeting.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ]


@app.post("/api/meetings/{mid}/loss-events")
def report_loss(
    mid: str,
    body: LossEvent,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    m = get_meeting(db, mid, actor)
    existing = db.scalar(
        select(AuditEvent).where(
            AuditEvent.meeting_id == mid,
            AuditEvent.action == "transcript.loss",
            AuditEvent.detail["id"].as_string() == body.id,
        )
    )
    if not existing:
        audit(db, mid, actor, "transcript.loss", body.model_dump())
        m.revision += 1
        db.commit()
    return {"id": body.id, "saved": True}


@app.post("/api/meetings/{mid}/manifest")
def save_manifest(
    mid: str, body: Manifest, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    m = get_meeting(db, mid, actor)
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
    m.revision += 1
    db.commit()
    return {"saved": True, "finalized": body.finalized}


@app.get("/api/meetings/{mid}")
def details(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    return view(get_meeting(db, mid, actor))


@app.post("/api/meetings/{mid}/start")
def start(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    m = get_meeting(db, mid, actor)
    if m.status != "created":
        raise HTTPException(409, "Meeting can only start from created status")
    # This starts the backend session, NOT the microphone. Client reports actual capture separately.
    m.status = "starting"
    m.started_at = now()
    m.revision += 1
    audit(db, mid, actor, "meeting.start_requested")
    db.commit()
    return view(m)


@app.post("/api/meetings/{mid}/capture-status")
def update_capture(
    mid: str,
    body: CaptureUpdate,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    m = get_meeting(db, mid, actor)
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


@app.post("/api/meetings/{mid}/pause")
def pause(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    m = get_meeting(db, mid, actor)
    if m.status != "recording":
        raise HTTPException(409, "Only recording meeting can be paused")
    m.status = "paused"
    m.capture_status = "paused"
    m.revision += 1
    audit(db, mid, actor, "meeting.paused")
    db.commit()
    return view(m)


@app.post("/api/meetings/{mid}/resume")
def resume(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    m = get_meeting(db, mid, actor)
    if m.status not in ("paused", "interrupted"):
        raise HTTPException(409, "Meeting is not paused/interrupted")
    m.status = "starting"
    m.capture_status = "unknown"
    m.revision += 1
    audit(db, mid, actor, "meeting.resume_requested")
    db.commit()
    return view(m)


@app.post("/api/meetings/{mid}/stop")
def stop(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    m = get_meeting(db, mid, actor)
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


@app.post("/api/speech/token")
async def token(actor: str = Depends(require_actor), settings: Settings = Depends(get_settings)):
    return await app.state.speech_tokens.get(settings)


@app.post("/api/meetings/{mid}/segments")
def segments(
    mid: str,
    body: SegmentBatch,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    m = get_meeting(db, mid, actor)
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
    if inserted:
        m.revision += 1
    db.commit()
    return {"accepted": inserted, "duplicates": len(body.segments) - inserted}


@app.get("/api/meetings/{mid}/transcript")
def transcript(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    get_meeting(db, mid, actor)
    ss = transcript_segments(db, mid)
    return {
        "meeting_id": mid,
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


@app.patch("/api/meetings/{mid}/speakers")
def rename_speaker(
    mid: str,
    body: SpeakerRename,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    m = get_meeting(db, mid, actor)
    if not db.scalar(
        select(Segment.id).where(Segment.meeting_id == mid, Segment.speaker_id == body.speaker_id)
    ):
        raise HTTPException(404, "Speaker has no saved transcript in this meeting yet")
    name = db.get(SpeakerName, (mid, body.speaker_id))
    if name:
        name.display_name = body.display_name
    else:
        db.add(SpeakerName(meeting_id=mid, **body.model_dump()))
    m.revision += 1
    audit(db, mid, actor, "speaker.renamed", body.model_dump())
    db.commit()
    return body.model_dump()


@app.post("/api/meetings/{mid}/audio-chunks", status_code=201)
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
    m = get_meeting(db, mid, actor)
    if m.status == "created":
        raise HTTPException(409, "Meeting not started")
    if sequence < 0 or start_ms < 0 or end_ms < start_ms:
        raise HTTPException(422, "Invalid chunk timeline")
    data = await audio.read(settings.max_audio_chunk_mb * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(422, "Audio chunk is empty")
    if len(data) > settings.max_audio_chunk_mb * 1024 * 1024:
        raise HTTPException(413, "Audio chunk too large")
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256.lower():
        raise HTTPException(422, "Audio checksum mismatch")
    existing = db.scalar(
        select(AudioChunk).where(AudioChunk.meeting_id == mid, AudioChunk.sequence == sequence)
    )
    if existing:
        if existing.sha256 != actual:
            raise HTTPException(409, "Chunk sequence already exists with different content")
        return {"id": existing.id, "duplicate": True}
    if m.status in ("processing", "ready_review"):
        raise HTTPException(409, "Synchronize all audio before generating minutes")
    typ = (audio.content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if typ not in (
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
        "application/octet-stream",
    ):
        raise HTTPException(415, "Unsupported audio format")
    folder = settings.storage_dir / mid
    folder.mkdir(parents=True, exist_ok=True)
    import shutil

    if shutil.disk_usage(folder).free < len(data) + 50 * 1024 * 1024:
        raise HTTPException(
            507,
            "Disk space is too low. Audio remains in the browser for retry after freeing space.",
        )
    destination = folder / f"{sequence:09d}.bin"
    temp = folder / f".{sequence:09d}.tmp"
    with temp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, destination)
    item = AudioChunk(
        meeting_id=mid,
        sequence=sequence,
        start_ms=start_ms,
        end_ms=end_ms,
        sha256=actual,
        path=str(destination),
        size_bytes=len(data),
        media_type=typ,
    )
    db.add(item)
    m.revision += 1
    db.flush()
    audit(db, mid, actor, "audio.chunk_committed", {"sequence": sequence, "size": len(data)})
    db.commit()
    return {"id": item.id, "duplicate": False}


@app.get("/api/meetings/{mid}/audio-chunks")
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


@app.post("/api/meetings/{mid}/mom/generate", status_code=202)
def generate(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    # Serialize the read-then-queue decision so simultaneous clicks cannot consume quota twice.
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    else:
        db.execute(select(Meeting.id).where(Meeting.id == mid).with_for_update())
    m = get_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
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
    previous = db.scalar(
        select(Job)
        .where(Job.meeting_id == mid, Job.kind == "generate_mom", Job.status == "failed")
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job = Job(
        meeting_id=mid,
        kind="generate_mom",
        checkpoint={**(previous.checkpoint or {}), "retry_count": 0} if previous else None,
    )
    db.add(job)
    m.status = "processing"
    m.revision += 1
    db.flush()
    audit(db, mid, actor, "mom.queued")
    db.commit()
    return {"job_id": job.id, "status": job.status}


@app.get("/api/meetings/{mid}/jobs")
def jobs(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    get_meeting(db, mid, actor)
    jj = db.scalars(select(Job).where(Job.meeting_id == mid).order_by(Job.created_at.desc())).all()
    return [
        {
            "id": j.id,
            "kind": j.kind,
            "status": j.status,
            "attempts": j.attempts,
            "error": j.error,
            "run_after": utc_time(j.run_after),
            "lease_until": utc_time(j.lease_until),
            "completed_sections": len((j.checkpoint or {}).get("sections", {})),
            "retry_attempts": (j.checkpoint or {}).get("retry_count", 0),
        }
        for j in jj
    ]


@app.get("/api/meetings/{mid}/mom")
def get_mom(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    get_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if not mom:
        raise HTTPException(404, "MoM not generated")
    return {
        "status": mom.status,
        "data": stored_minutes(mom.data),
        "approved_by": mom.approved_by,
        "updated_at": utc_time(mom.updated_at),
    }


def save_revision(db, mom, actor, provenance=None):
    db.add(
        MomRevision(
            meeting_id=mom.meeting_id,
            data=mom.data,
            status=mom.status,
            actor=actor,
            provenance=provenance or {"schema_version": 1},
        )
    )


@app.get("/api/meetings/{mid}/mom/revisions")
def revisions(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    get_meeting(db, mid, actor)
    return [
        {
            "id": r.id,
            "data": stored_minutes(r.data),
            "status": r.status,
            "at": utc_time(r.created_at),
            "provenance": r.provenance,
        }
        for r in db.scalars(
            select(MomRevision)
            .where(MomRevision.meeting_id == mid)
            .order_by(MomRevision.created_at.desc())
        )
    ]


@app.post("/api/meetings/{mid}/mom/revise")
def revise(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    m = get_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if not mom or mom.status != "approved":
        raise HTTPException(409, "Only approved minutes need a new revision")
    save_revision(db, mom, actor)
    mom.data = stored_minutes(mom.data)
    mom.status, mom.approved_by, mom.updated_at = "draft", None, now()
    m.revision += 1
    audit(db, mid, actor, "mom.revision_started")
    db.commit()
    return {"status": mom.status, "data": stored_minutes(mom.data)}


@app.patch("/api/meetings/{mid}/mom")
def edit_mom(
    mid: str, body: MomData, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    get_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if not mom:
        raise HTTPException(404, "MoM not generated")
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
            body.model_dump(), list(db.scalars(select(Segment.id).where(Segment.meeting_id == mid)))
        )
    except ValueError:
        raise HTTPException(422, "Minutes cite unknown or invalid transcript evidence") from None
    save_revision(db, mom, actor)
    mom.data = body.model_dump()
    mom.updated_at = now()
    audit(db, mid, actor, "mom.edited")
    db.get(Meeting, mid).revision += 1
    db.commit()
    return {"status": mom.status, "data": stored_minutes(mom.data)}


@app.post("/api/meetings/{mid}/mom/approve")
def approve(
    mid: str, body: Approval, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    m = get_meeting(db, mid, actor)
    mom = db.get(Mom, mid)
    if not mom:
        raise HTTPException(404, "MoM not generated")
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
    save_revision(db, mom, actor)
    m.revision += 1
    audit(db, mid, actor, "mom.approved")
    db.commit()
    return {"status": "approved"}


@app.get("/api/meetings/{mid}/audit")
def audits(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    get_meeting(db, mid, actor)
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.meeting_id == mid).order_by(AuditEvent.created_at)
    ).all()
    return [
        {"action": e.action, "at": utc_time(e.created_at), "actor": e.actor, "detail": e.detail}
        for e in events
    ]


@app.get("/api/meetings/{mid}/coverage")
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


@app.post("/api/meetings/{mid}/recover", status_code=202)
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
    job = Job(meeting_id=mid, kind="recover_transcript")
    db.add(job)
    db.flush()
    audit(db, mid, actor, "transcript.recovery_queued")
    db.commit()
    return {"job_id": job.id, "status": job.status}


@app.get("/api/meetings/{mid}/snapshot")
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
        "minutes": {
            "status": mom.status,
            "data": stored_minutes(mom.data),
            "approved_by": mom.approved_by,
        }
        if mom
        else None,
        "jobs": latest_jobs,
    }
