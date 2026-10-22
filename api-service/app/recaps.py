import asyncio
import hashlib
import subprocess
import tempfile
import wave
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import response_models as contracts
from .audio_store import storage_key, storage_path
from .config import Settings, get_settings
from .database import db_session
from .models import (
    AudioChunk,
    AuditEvent,
    CaptureManifest,
    Job,
    Meeting,
    Mom,
    NotesFinalization,
    PersonalRecap,
    Segment,
    now,
)
from .providers import fast_transcribe_file
from .security import require_actor

router = APIRouter(prefix="/api/recaps", tags=["Personal recaps"])


class RecapCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    id: UUID
    title: str = Field(min_length=1, max_length=255)
    language: str = Field(default="auto", min_length=1, max_length=20)
    meeting_date: date | None = None
    participants: list[str] = Field(default_factory=list, max_length=100)


class RecapNotes(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    notes: str = Field(max_length=20000)
    reviewed: bool = False
    revision: int = Field(ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    meeting_date: date | None = None
    participants: list[str] | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, min_length=1, max_length=20)


def owned(db, mid, actor):
    meeting, recap = db.get(Meeting, mid), db.get(PersonalRecap, mid)
    if not meeting or not recap:
        raise HTTPException(404, "Personal recap not found")
    if meeting.created_by != actor:
        raise HTTPException(403, "Personal recap access denied")
    return meeting, recap


def recap_view(db, meeting, recap):
    audio = db.scalar(select(AudioChunk).where(AudioChunk.meeting_id == meeting.id))
    memo = db.get(Mom, meeting.id)
    final = db.get(NotesFinalization, meeting.id)
    final_job = db.get(Job, final.job_id) if final else None
    return {
        "id": meeting.id,
        "title": meeting.title,
        "language": meeting.language,
        "meeting_date": recap.meeting_date,
        "participants": recap.participants,
        "notes": recap.notes,
        "reviewed": recap.reviewed,
        "revision": recap.revision,
        "audio_available": bool(
            audio and audio.storage_state == "committed" and storage_path(audio.path).is_file()
        ),
        "memo_status": memo.status if memo else None,
        "source_label": "Based on personal recollection",
        "finalization": None
        if not final
        else {
            "status": "accepted" if final.accepted else final_job.status if final_job else "failed",
            "live_notes": final.live_notes,
            "final_notes": final.final_notes,
        },
    }


def busy(db, mid):
    return db.scalar(
        select(Job).where(Job.meeting_id == mid, Job.status.in_(["pending", "running"]))
    )


def lock_recap(db, mid):
    if db.bind.dialect.name == "sqlite":
        if not db.connection().connection.driver_connection.in_transaction:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    else:
        db.execute(select(Meeting.id).where(Meeting.id == mid).with_for_update())


def editable(db, mid):
    mom = db.get(Mom, mid)
    if mom and mom.status == "approved":
        raise HTTPException(409, "Create a new memo revision before editing recap notes.")
    if busy(db, mid):
        raise HTTPException(409, "Wait for transcription or memo generation to finish.")


@router.post(
    "", status_code=201, response_model=contracts.RecapView, response_model_exclude_unset=True
)
def create(
    body: RecapCreate, db: Session = Depends(db_session), actor: str = Depends(require_actor)
):
    mid = str(body.id)
    lock_recap(db, mid)
    if db.get(Meeting, mid):
        meeting, recap = owned(db, mid, actor)
        return recap_view(db, meeting, recap)
    participants = list(dict.fromkeys(p.strip() for p in body.participants if p.strip()))
    if any(len(p) > 100 for p in participants):
        raise HTTPException(422, "Participant names must be 100 characters or fewer.")
    meeting = Meeting(
        id=mid,
        title=body.title,
        source="personal-recap",
        language=body.language,
        created_by=actor,
        consent_confirmed=False,
        status="stopped",
        capture_status="stopped",
        sync_status="synced",
        ended_at=now(),
    )
    recap = PersonalRecap(
        meeting_id=mid,
        meeting_date=body.meeting_date.isoformat() if body.meeting_date else None,
        participants=participants,
    )
    db.add(meeting)
    db.flush()
    db.add(recap)
    db.add(
        AuditEvent(
            meeting_id=mid,
            actor=actor,
            action="recap.created",
            detail={
                "source_kind": "personal_recollection",
                "meeting_date": recap.meeting_date,
                "participants": participants,
            },
        )
    )
    db.commit()
    return recap_view(db, meeting, recap)


@router.get("/{mid}", response_model=contracts.RecapView, response_model_exclude_unset=True)
def details(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    return recap_view(db, *owned(db, mid, actor))


@router.patch("/{mid}/notes", response_model=contracts.RecapView, response_model_exclude_unset=True)
def notes(
    mid: str,
    body: RecapNotes,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    lock_recap(db, mid)
    meeting, recap = owned(db, mid, actor)
    editable(db, mid)
    if recap.revision != body.revision:
        raise HTTPException(409, "Recap notes changed in another tab. Reload before saving.")
    if body.reviewed and not body.notes:
        raise HTTPException(422, "Enter notes before marking them reviewed.")
    participants = (
        None
        if body.participants is None
        else list(dict.fromkeys(p.strip() for p in body.participants if p.strip()))
    )
    if participants and any(len(p) > 100 for p in participants):
        raise HTTPException(422, "Participant names must be 100 characters or fewer.")
    changed = recap.notes != body.notes or recap.reviewed != body.reviewed
    if body.title is not None and meeting.title != body.title:
        meeting.title, changed = body.title, True
    if body.language is not None and meeting.language != body.language:
        meeting.language, changed = body.language, True
    if "meeting_date" in body.model_fields_set:
        meeting_date = body.meeting_date.isoformat() if body.meeting_date else None
        if recap.meeting_date != meeting_date:
            recap.meeting_date, changed = meeting_date, True
    if participants is not None and recap.participants != participants:
        recap.participants, changed = participants, True
    if changed:
        segment = Segment(
            meeting_id=mid,
            client_event_id=f"recap-notes:{uuid4()}",
            text=body.notes,
            start_ms=0,
            end_ms=0,
            source="recap",
        )
        if body.notes:
            db.add(segment)
            db.flush()
        recap.notes, recap.reviewed = body.notes, body.reviewed
        recap.notes_segment_id = segment.id if body.notes else None
        recap.revision += 1
        from .mutations import changed_evidence

        changed_evidence(meeting)
        meeting.status = "stopped"
        mom = db.get(Mom, mid)
        if mom:
            mom.status = "outdated"
        db.add(
            AuditEvent(
                meeting_id=mid,
                actor=actor,
                action="recap.notes_saved",
                detail={
                    "revision": recap.revision,
                    "reviewed": recap.reviewed,
                    "notes_segment_id": recap.notes_segment_id,
                },
            )
        )
        db.commit()
    return recap_view(db, meeting, recap)


def queue_transcription(db, meeting):
    pending = busy(db, meeting.id)
    if pending:
        if pending.kind != "transcribe_recap":
            raise HTTPException(409, "Wait for memo generation to finish.")
        return {"job_id": pending.id, "status": pending.status}
    from .limits import admit_job

    admit_job(db, meeting)
    job = Job(
        meeting_id=meeting.id,
        kind="transcribe_recap",
        owner_actor=meeting.created_by,
        input_evidence_revision=meeting.evidence_revision,
    )
    db.add(job)
    meeting.status, meeting.stt_status = "processing", "reconnecting"
    meeting.revision += 1
    db.flush()
    db.commit()
    return {"job_id": job.id, "status": job.status}


@router.post(
    "/{mid}/audio",
    status_code=202,
    response_model=contracts.QueueAck | contracts.SavedStatus,
    response_model_exclude_unset=True,
)
async def upload(
    mid: str,
    audio: UploadFile = File(...),
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
    settings: Settings = Depends(get_settings),
    transcribe: bool = Form(default=True),
):
    content_type = (audio.content_type or "application/octet-stream").split(";")[0].lower()
    extension = Path(audio.filename or "").suffix.lower()
    if content_type not in {
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/x-m4a",
        "video/mp4",
        "audio/aac",
        "audio/flac",
        "audio/x-flac",
        "audio/amr",
        "audio/opus",
        "application/octet-stream",
    } or extension not in {
        ".wav",
        ".webm",
        ".ogg",
        ".mp3",
        ".m4a",
        ".mp4",
        ".aac",
        ".flac",
        ".amr",
        ".opus",
    }:
        raise HTTPException(
            415, "Upload a voice note: WAV, MP3, M4A, MP4, WebM, OGG, AAC, FLAC, AMR or OPUS."
        )
    if content_type == "application/octet-stream":
        content_type = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".mp4": "audio/mp4",
            ".webm": "audio/webm",
            ".ogg": "audio/ogg",
            ".aac": "audio/aac",
            ".flac": "audio/flac",
            ".amr": "audio/amr",
            ".opus": "audio/opus",
        }[extension]
    from .audio_store import complete_upload

    return await asyncio.to_thread(
        complete_upload,
        lambda: _save_upload(mid, audio.file, content_type, transcribe, db, actor, settings),
        db,
        audio.file,
    )


def _save_upload(mid, stream, content_type, transcribe, db, actor, settings):
    from .audio_store import (
        abort_reservation,
        finish_reservation,
        reserve,
        stream_size,
        write_reserved,
    )
    from .mutations import changed_evidence, lock_meeting

    size = stream_size(stream)
    if not size:
        raise HTTPException(422, "The voice note is empty.")
    if size > settings.max_recap_audio_mb * 1024**2:
        raise HTTPException(413, "Voice note exceeds the configured upload limit.")
    checksum = hashlib.sha256()
    while block := stream.read(65536):
        checksum.update(block)
    digest = checksum.hexdigest()
    stream.seek(0)
    lock_recap(db, mid)
    meeting, recap = owned(db, mid, actor)
    existing = db.scalar(select(AudioChunk).where(AudioChunk.meeting_id == mid))
    if existing:
        if existing.sha256 != digest:
            raise HTTPException(
                409,
                "This recap already has a voice note. Create a new recap for another recording.",
            )
        if not transcribe:
            return {"status": "saved"}
        job = db.scalar(
            select(Job)
            .where(Job.meeting_id == mid, Job.kind == "transcribe_recap")
            .order_by(Job.created_at.desc())
        )
        if job:
            return {"job_id": job.id, "status": job.status}
        editable(db, mid)
        if recap.notes:
            raise HTTPException(
                409, "Notes already exist. Create a new recap for another voice note."
            )
        return queue_transcription(db, meeting)
    editable(db, mid)
    if transcribe and recap.notes:
        raise HTTPException(
            409,
            "This recap already has notes. Create a new recap to transcribe another voice note.",
        )
    reservation = reserve(db, meeting, "recap", size, settings, expected_sha=digest)
    try:
        digest, size = write_reserved(reservation, stream, settings.max_recap_audio_mb * 1024**2)
        meeting = lock_meeting(db, mid, actor)
        editable(db, mid)
        target = settings.storage_dir / mid / "recap-audio.bin"
        finish_reservation(db, reservation, target, digest, size)
        changed_evidence(meeting)
        db.add(
            AudioChunk(
                meeting_id=mid,
                sequence=0,
                start_ms=0,
                end_ms=0,
                sha256=digest,
                media_type=content_type,
                path=storage_key(target, settings),
                size_bytes=size,
            )
        )
        db.add(
            AuditEvent(
                meeting_id=mid,
                actor=actor,
                action="recap.audio_uploaded",
                detail={"sha256": digest},
            )
        )
        if transcribe:
            return queue_transcription(db, meeting)
        db.commit()
        return {"status": "saved"}
    except BaseException:
        abort_reservation(db, reservation)
        raise


@router.post(
    "/{mid}/transcribe",
    status_code=202,
    response_model=contracts.QueueAck,
    response_model_exclude_unset=True,
)
def retry(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    lock_recap(db, mid)
    meeting, recap = owned(db, mid, actor)
    if busy(db, mid):
        return queue_transcription(db, meeting)
    editable(db, mid)
    if recap.notes:
        raise HTTPException(
            409, "Notes already exist. Edit and review them before generating your memo."
        )
    if not db.scalar(select(AudioChunk.id).where(AudioChunk.meeting_id == mid)):
        raise HTTPException(409, "Upload or record a voice note first.")
    return queue_transcription(db, meeting)


@router.get(
    "/{mid}/audio",
    response_class=FileResponse,
    responses={
        200: {
            "description": "Original stored media; Content-Type follows the validated upload",
            "content": {
                "audio/*": {"schema": {"type": "string", "format": "binary"}},
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
def download(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    owned(db, mid, actor)
    audio = db.scalar(select(AudioChunk).where(AudioChunk.meeting_id == mid))
    if not audio or audio.storage_state != "committed" or not storage_path(audio.path).is_file():
        raise HTTPException(404, "Voice note is not available.")
    return FileResponse(storage_path(audio.path), media_type=audio.media_type)


@router.post(
    "/{mid}/finalize",
    status_code=202,
    response_model=contracts.QueueAck,
    response_model_exclude_unset=True,
)
def finalize(mid: str, db: Session = Depends(db_session), actor: str = Depends(require_actor)):
    lock_recap(db, mid)
    meeting, recap = owned(db, mid, actor)
    previous = db.get(NotesFinalization, mid)
    if previous:
        job = db.get(Job, previous.job_id)
        if previous.accepted or (job and job.status != "failed"):
            return {"job_id": previous.job_id, "status": job.status if job else "completed"}
    editable(db, mid)
    if not db.scalar(select(AudioChunk.id).where(AudioChunk.meeting_id == mid)):
        raise HTTPException(409, "Save the completed audio before finalizing.")
    from .limits import admit_job

    admit_job(db, meeting)
    job = Job(
        meeting_id=mid,
        kind="transcribe_recap",
        owner_actor=actor,
        input_evidence_revision=meeting.evidence_revision,
        checkpoint={"finalize": True},
    )
    db.add(job)
    db.flush()
    if previous:
        previous.job_id = job.id
    else:
        db.add(NotesFinalization(meeting_id=mid, job_id=job.id, live_notes=recap.notes))
    recap.reviewed = False
    meeting.status = "processing"
    db.commit()
    return {"job_id": job.id, "status": job.status}


class FinalChoice(BaseModel):
    choice: str = Field(pattern="^(current|audio)$")
    revision: int = Field(ge=0)


@router.post(
    "/{mid}/finalize/choose", response_model=contracts.RecapView, response_model_exclude_unset=True
)
def choose_final(
    mid: str,
    body: FinalChoice,
    db: Session = Depends(db_session),
    actor: str = Depends(require_actor),
):
    lock_recap(db, mid)
    meeting, recap = owned(db, mid, actor)
    editable(db, mid)
    final = db.get(NotesFinalization, mid)
    if not final or not final.final_notes:
        raise HTTPException(409, "Wait for the audio transcript before choosing a version.")
    if recap.revision != body.revision:
        raise HTTPException(409, "Notes changed. Reload before choosing a transcript.")
    if body.choice == "audio":
        segment = Segment(
            meeting_id=mid,
            client_event_id=f"notes-final:{uuid4()}",
            text=final.final_notes,
            source="recap",
            start_ms=0,
            end_ms=0,
        )
        db.add(segment)
        db.flush()
        recap.notes, recap.notes_segment_id = final.final_notes, segment.id
    final.accepted = True
    mom = db.get(Mom, mid)
    if mom:
        mom.status = "outdated"
    recap.reviewed = False
    recap.revision += 1
    from .mutations import changed_evidence

    changed_evidence(meeting)
    meeting.status = "stopped"
    db.add(
        AuditEvent(
            meeting_id=mid,
            actor=actor,
            action="notes.finalization_chosen",
            detail={"choice": body.choice},
        )
    )
    db.commit()
    return recap_view(db, meeting, recap)


def decode_voice_note(path):
    from imageio_ffmpeg import get_ffmpeg_exe

    with tempfile.TemporaryDirectory(prefix="personal-recap-") as folder:
        output = Path(folder) / "voice.wav"
        try:
            subprocess.run(
                [
                    get_ffmpeg_exe(),
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    "wav,matroska,webm,ogg,mov,mp3,aac,flac,amr",
                    "-threads",
                    "1",
                    "-i",
                    path,
                    "-vn",
                    "-fs",
                    str(get_settings().max_decoded_audio_bytes),
                    "-t",
                    "1801",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(output),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if output.stat().st_size >= get_settings().max_decoded_audio_bytes:
                raise RuntimeError("Decoded audio exceeds the configured resource limit")
            with wave.open(str(output)) as audio:
                duration = audio.getnframes() / audio.getframerate() * 1000
        except (OSError, subprocess.SubprocessError, wave.Error, EOFError):
            raise RuntimeError(
                "The voice note could not be decoded. Keep the original and upload a valid audio file."
            ) from None
        if duration <= 0 or duration > 1800000:
            raise RuntimeError("Voice notes must contain audio and be 30 minutes or shorter.")
        return output.read_bytes(), duration


async def transcribe_recap(mid, jid, claim):
    from .database import SessionLocal

    with SessionLocal() as db:
        meeting, recap = db.get(Meeting, mid), db.get(PersonalRecap, mid)
        evidence_revision = meeting.evidence_revision if meeting else None
        initial_job = db.get(Job, jid)
        finalizing = bool(initial_job and (initial_job.checkpoint or {}).get("finalize"))
        audio = db.scalar(select(AudioChunk).where(AudioChunk.meeting_id == mid))
        if not meeting or not recap or not audio or (recap.notes and not finalizing):
            raise RuntimeError("The recap has no pending voice note to transcribe.")
        content = storage_path(audio.path).read_bytes()
        if hashlib.sha256(content).hexdigest() != audio.sha256:
            raise RuntimeError("Voice note checksum failed. Restore a verified recording.")
        path, language, digest, revision = (
            str(storage_path(audio.path)),
            meeting.language,
            audio.sha256,
            recap.revision,
        )
    wav, duration = await asyncio.to_thread(decode_voice_note, path)
    result = await fast_transcribe_file(get_settings(), wav, "recap.wav", "audio/wav", language)
    texts = []
    for phrase in result.get("phrases", []):
        text = phrase.get("text", "").strip()
        start, length = phrase.get("offsetMilliseconds"), phrase.get("durationMilliseconds")
        if not text:
            continue
        if (
            not isinstance(start, (int, float))
            or not isinstance(length, (int, float))
            or not 0 <= start <= start + length <= duration + 1000
        ):
            raise RuntimeError(
                "Transcription returned invalid recap timing. Your voice note is retained."
            )
        texts.append(text)
    notes = "\n".join(texts)
    if not notes:
        raise RuntimeError(
            "No speech was transcribed. Listen to your voice note or type your recap notes."
        )
    if len(notes) > 20000:
        raise RuntimeError(
            "The transcribed recap is too long. Keep the audio and use a shorter voice note."
        )
    with SessionLocal() as db:
        from .mutations import changed_evidence, finish_job, lock_meeting

        locked_meeting = lock_meeting(db, mid)
        if locked_meeting.evidence_revision != evidence_revision:
            raise RuntimeError("Meeting evidence changed during processing; original data retained")
        job, recap, meeting = db.get(Job, jid), db.get(PersonalRecap, mid), db.get(Meeting, mid)
        if (
            not job
            or job.claim_token != claim
            or job.status != "running"
            or recap.revision != revision
        ):
            raise RuntimeError("Recap changed during transcription; your edits were kept.")
        if finalizing:
            final = db.get(NotesFinalization, mid)
            if not final or final.job_id != jid:
                raise RuntimeError("Finalization changed; previous notes were retained.")
            final.final_notes = notes
            recap.reviewed = False
            meeting.status = "stopped"
            db.add(
                AuditEvent(
                    meeting_id=mid,
                    actor="system",
                    action="notes.audio_finalized",
                    detail={"audio_sha256": digest},
                )
            )
            changed_evidence(locked_meeting)
            finish_job(db, jid, claim)
            db.commit()
            return
        segment = Segment(
            meeting_id=mid,
            client_event_id=f"recap-audio:{jid}",
            text=notes,
            start_ms=0,
            end_ms=0,
            source="recap",
        )
        db.add(segment)
        db.flush()
        recap.notes, recap.reviewed, recap.notes_segment_id = notes, False, segment.id
        recap.revision += 1
        source_audio = db.scalar(select(AudioChunk).where(AudioChunk.meeting_id == mid))
        source_audio.end_ms = round(duration)
        db.add(
            CaptureManifest(
                meeting_id=mid,
                data={
                    "expected_chunks": 1,
                    "recorded_through_ms": round(duration),
                    "finalized": True,
                    "pauses": [],
                },
            )
        )
        meeting.status, meeting.stt_status = "stopped", "connected"
        db.add(
            AuditEvent(
                meeting_id=mid,
                actor="system",
                action="recap.transcribed",
                detail={
                    "audio_sha256": digest,
                    "audio_duration_ms": round(duration),
                    "source_kind": "personal_recollection",
                    "notes_segment_id": segment.id,
                },
            )
        )
        changed_evidence(locked_meeting)
        finish_job(db, jid, claim)
        db.commit()
