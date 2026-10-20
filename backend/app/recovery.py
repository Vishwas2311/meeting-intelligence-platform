"""Verified recovery uses the complete saved recording; silence is never treated as a gap."""

import asyncio
import hashlib
import json
import subprocess
import tempfile
import wave
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .models import AudioChunk, AuditEvent, CaptureManifest, Meeting, Mom, MomRevision, Segment
from .providers import fast_transcribe_file


def loss_ids(db, mid):
    return {
        event.id
        for event in db.scalars(select(AuditEvent).where(AuditEvent.meeting_id == mid))
        if event.action == "transcript.loss"
        or (event.action == "capture.status" and event.detail.get("transcript_incomplete"))
    }


def has_transcript_loss(db, mid: str) -> bool:
    losses = loss_ids(db, mid)
    recovered = set()
    for detail in db.scalars(
        select(AuditEvent.detail).where(
            AuditEvent.meeting_id == mid, AuditEvent.action == "transcript.recovered"
        )
    ):
        recovered.update(detail.get("loss_ids", []))
    return bool(losses - recovered)


def transcript_segments(db, mid):
    recovery = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.meeting_id == mid, AuditEvent.action == "transcript.recovered")
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    query = select(Segment).where(Segment.meeting_id == mid)
    if recovery:
        query = query.where(Segment.id.in_(recovery.detail["segment_ids"]))
    return db.scalars(query.order_by(Segment.start_ms, Segment.created_at)).all()


def audio_coverage(db, mid):
    manifest = db.get(CaptureManifest, mid)
    chunks = db.scalars(
        select(AudioChunk).where(AudioChunk.meeting_id == mid).order_by(AudioChunk.sequence)
    ).all()
    data = manifest.data if manifest else None
    gaps = []
    cursor = 0
    pauses = data.get("pauses", []) if data else []
    for chunk in chunks:
        if chunk.start_ms > cursor + 250 and not any(
            p["start_ms"] <= cursor + 250 and p["end_ms"] >= chunk.start_ms - 250 for p in pauses
        ):
            gaps.append({"start_ms": cursor, "end_ms": chunk.start_ms})
        cursor = max(cursor, chunk.end_ms)
    complete = bool(
        data
        and data["finalized"]
        and chunks
        and [c.sequence for c in chunks] == list(range(data["expected_chunks"]))
        and abs(cursor - data["recorded_through_ms"]) <= 250
        and not gaps
    )
    return {
        "manifest": data,
        "audio_timeline_gaps": gaps,
        "persisted_audio_chunks": len(chunks),
        "persisted_audio_through_ms": cursor,
        "audio_capture_claimed_complete": complete,
        "transcript_incomplete": has_transcript_loss(db, mid),
    }


def require_complete_audio(db, mid):
    coverage = audio_coverage(db, mid)
    if coverage["manifest"] and not coverage["audio_capture_claimed_complete"]:
        manifest = coverage["manifest"]
        present = set(db.scalars(select(AudioChunk.sequence).where(AudioChunk.meeting_id == mid)))
        retired = {
            detail["sequence"]
            for detail in db.scalars(
                select(AuditEvent.detail).where(
                    AuditEvent.meeting_id == mid, AuditEvent.action == "audio.retention"
                )
            )
        }
        approved = db.scalar(
            select(MomRevision.id)
            .where(MomRevision.meeting_id == mid, MomRevision.status == "approved")
            .limit(1)
        )
        missing = set(range(manifest["expected_chunks"])) - present
        # Intentional expiry of previously verified, approved audio does not delete text evidence.
        if manifest["finalized"] and approved and missing and missing <= retired:
            return coverage
        raise RuntimeError(
            "Recording manifest is incomplete or audio chunks are missing. Synchronize this browser before continuing."
        )
    return coverage


def fingerprint(db, mid):
    manifest = db.get(CaptureManifest, mid)
    chunks = db.scalars(
        select(AudioChunk).where(AudioChunk.meeting_id == mid).order_by(AudioChunk.sequence)
    ).all()
    value = [
        manifest.data if manifest else None,
        [[c.sequence, c.sha256, c.start_ms, c.end_ms] for c in chunks],
        sorted(loss_ids(db, mid)),
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def decode_audio(chunks, manifest):
    from imageio_ffmpeg import get_ffmpeg_exe

    if sum(c.size_bytes for c in chunks) > 250 * 1024 * 1024:
        raise RuntimeError(
            "Recording exceeds the 250 MB recovery limit. Export for manual recovery."
        )
    with tempfile.TemporaryDirectory(prefix="meeting-recovery-") as folder:
        source, output = Path(folder) / "capture.bin", Path(folder) / "capture.wav"
        with source.open("wb") as dest:
            for chunk in chunks:
                content = Path(chunk.path).read_bytes()
                if hashlib.sha256(content).hexdigest() != chunk.sha256:
                    raise RuntimeError("Saved audio checksum failed; restore a verified backup.")
                dest.write(content)
        try:
            subprocess.run(
                [
                    get_ffmpeg_exe(),
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-vn",
                    "-af",
                    "asetpts=N/SR/TB",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (subprocess.SubprocessError, OSError):
            raise RuntimeError(
                "Saved audio could not be decoded. Keep the recording for recovery."
            ) from None
        with wave.open(str(output)) as audio:
            duration = audio.getnframes() / audio.getframerate() * 1000
        expected = manifest["recorded_through_ms"] - sum(
            p["end_ms"] - p["start_ms"] for p in manifest["pauses"]
        )
        if duration > 7200000 or abs(duration - expected) > max(300, expected * 0.02):
            raise RuntimeError(
                f"Decoded audio duration ({round(duration)} ms) does not match the recording manifest ({expected} ms); coverage remains incomplete."
            )
        return output.read_bytes(), duration


def wall_time(offset, pauses):
    result = offset
    for pause in pauses:
        if result >= pause["start_ms"]:
            result += pause["end_ms"] - pause["start_ms"]
    return round(result)


async def recover_transcript(mid, jid, claim):
    from .database import SessionLocal
    from .models import Job

    with SessionLocal() as db:
        meeting = db.get(Meeting, mid)
        if not meeting or meeting.status not in ("stopped", "processing", "ready_review"):
            raise RuntimeError("Stop recording before recovery")
        mom = db.get(Mom, mid)
        if mom and mom.status == "approved":
            raise RuntimeError("Create a new revision before recovery")
        coverage = require_complete_audio(db, mid)
        if not coverage["audio_capture_claimed_complete"]:
            raise RuntimeError(
                "A finalized recording manifest is required for automatic recovery. Legacy recordings need manual verification."
            )
        manifest = coverage["manifest"]
        chunks = db.scalars(
            select(AudioChunk).where(AudioChunk.meeting_id == mid).order_by(AudioChunk.sequence)
        ).all()
        before, losses, language = fingerprint(db, mid), sorted(loss_ids(db, mid)), meeting.language
    audio, duration = await asyncio.to_thread(decode_audio, chunks, manifest)
    result = await fast_transcribe_file(get_settings(), audio, "meeting.wav", "audio/wav", language)
    recovered = []
    for phrase in result.get("phrases", []):
        text = phrase.get("text", "").strip()
        start, length = phrase.get("offsetMilliseconds"), phrase.get("durationMilliseconds")
        if not text:
            continue
        if (
            not isinstance(start, (int, float))
            or not isinstance(length, (int, float))
            or start < 0
            or length < 0
            or start + length > duration + 1000
        ):
            raise RuntimeError("Fast transcription returned invalid timing; recording retained.")
        recovered.append(
            Segment(
                meeting_id=mid,
                client_event_id=f"recovery:{jid}:{len(recovered)}",
                text=text,
                start_ms=wall_time(start, manifest["pauses"]),
                end_ms=wall_time(start + length, manifest["pauses"]),
                source="recovered",
            )
        )
    if not recovered:
        raise RuntimeError(
            "No timed speech was recovered. Coverage remains unverified; keep the audio."
        )
    with SessionLocal() as db:
        job, meeting, mom = db.get(Job, jid), db.get(Meeting, mid), db.get(Mom, mid)
        if (
            not job
            or job.claim_token != claim
            or job.status != "running"
            or fingerprint(db, mid) != before
            or (mom and mom.status == "approved")
        ):
            raise RuntimeError("Recording changed during recovery; no transcript was replaced.")
        db.add_all(recovered)
        db.flush()
        db.add(
            AuditEvent(
                meeting_id=mid,
                actor="system",
                action="transcript.recovered",
                detail={
                    "loss_ids": losses,
                    "fingerprint": before,
                    "segment_ids": [s.id for s in recovered],
                },
            )
        )
        meeting.capture_status, meeting.sync_status, meeting.status = "stopped", "synced", "stopped"
        meeting.revision += 1
        if mom:
            mom.status = "outdated"
        db.commit()
