"""Bounded filesystem writes and durable Oracle/SQLite upload reservations."""

import hashlib
import os
import shutil
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


class UploadPending(HTTPException):
    def __init__(self):
        super().__init__(
            409, "Upload is pending reconciliation; retry later", headers={"Retry-After": "1"}
        )


def storage_path(value, settings=None):
    from .config import get_settings

    settings = settings or get_settings()
    root = settings.storage_dir.resolve()
    path = Path(value)
    absolute = path.is_absolute()
    resolved = path.resolve() if absolute else (root / path).resolve()
    if not resolved.is_relative_to(root) and (not absolute or settings.app_env == "production"):
        raise RuntimeError("Audio path is outside the mounted volume")
    return resolved


def storage_key(path, settings=None):
    from .config import get_settings

    root = (settings or get_settings()).storage_dir.resolve()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise RuntimeError("Audio destination is outside the mounted volume")
    return resolved.relative_to(root).as_posix()


def complete_upload(operation, db, stream):
    import time

    started = time.monotonic()
    position = stream.tell()
    while True:
        try:
            return operation()
        except UploadPending:
            db.rollback()  # Never wait while holding the meeting or owner lock.
            stream.seek(position)
            if time.monotonic() - started >= 2:
                raise
            time.sleep(0.05)


def check_volume(settings):
    root = settings.storage_dir
    if not root.is_dir():
        raise RuntimeError("Audio volume is unavailable")
    if settings.app_env == "production" and not (root / settings.storage_mount_marker).is_file():
        raise RuntimeError("Expected persistent audio volume is not mounted")
    if not os.access(root, os.R_OK | os.W_OK):
        raise RuntimeError("Audio volume permissions are invalid")


def actor_usage(db, actor):
    from .models import ActorUsage, AudioChunk, Meeting

    row = db.scalar(select(ActorUsage).where(ActorUsage.actor == actor).with_for_update())
    if row is None:
        total = db.scalar(
            select(func.coalesce(func.sum(AudioChunk.size_bytes), 0))
            .join(Meeting, Meeting.id == AudioChunk.meeting_id)
            .where(Meeting.created_by == actor, AudioChunk.storage_state != "retired")
        )
        try:
            with db.begin_nested():
                db.add(ActorUsage(actor=actor, used_bytes=total, reserved_bytes=0))
                db.flush()
        except IntegrityError:
            pass
        row = db.scalar(select(ActorUsage).where(ActorUsage.actor == actor).with_for_update())
    return row


def reserve(db, meeting, slot, size, settings, expected_sha="pending"):
    from .models import AudioChunk, UploadReservation, now

    check_volume(settings)
    used = actor_usage(db, meeting.created_by)
    existing = db.scalar(
        select(UploadReservation).where(
            UploadReservation.meeting_id == meeting.id, UploadReservation.slot == slot
        )
    )
    if existing and existing.state == "pending":
        raise UploadPending()
    if used.used_bytes + used.reserved_bytes + size > settings.actor_storage_bytes:
        raise HTTPException(429, "Storage allowance exceeded", headers={"Retry-After": "60"})
    if used.reserved_bytes + size > settings.max_reserved_upload_bytes:
        raise HTTPException(
            429, "Outstanding upload allowance exceeded", headers={"Retry-After": "30"}
        )
    total = db.scalar(
        select(func.coalesce(func.sum(AudioChunk.size_bytes), 0)).where(
            AudioChunk.meeting_id == meeting.id, AudioChunk.storage_state != "retired"
        )
    )
    if slot != "recap" and total + size > settings.max_meeting_audio_bytes:
        raise HTTPException(413, "Meeting audio exceeds the 250 MiB recovery limit")
    if existing:
        db.delete(existing)
        db.flush()
    folder = (settings.storage_dir / meeting.id).resolve()
    if not folder.is_relative_to(settings.storage_dir.resolve()):
        raise HTTPException(422, "Invalid recording path")
    new_folder = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    if new_folder and os.name != "nt":
        parent = os.open(str(folder.parent), os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    if shutil.disk_usage(folder).free < size + 100 * 1024**2:
        raise HTTPException(507, "Free disk space before uploading; keep the local recording")
    item = UploadReservation(
        actor=meeting.created_by,
        meeting_id=meeting.id,
        slot=slot,
        size_bytes=size,
        sha256=expected_sha,
        path=storage_key(folder / f".{uuid4()}.tmp", settings),
        expires_at=now() + timedelta(minutes=15),
    )
    db.add(item)
    used.reserved_bytes += size
    db.commit()
    return item


def stream_size(stream):
    current = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(current)
    return size - current


def write_reserved(item, stream, maximum):
    path = storage_path(item.path)
    size, digest = 0, hashlib.sha256()
    try:
        with path.open("xb") as output:
            while block := stream.read(64 * 1024):
                size += len(block)
                if size > maximum or size > item.size_bytes:
                    raise HTTPException(413, "Audio upload too large")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if not size:
            raise HTTPException(422, "Audio is empty")
        return digest.hexdigest(), size
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def finish_reservation(db, item, destination, digest, size):
    from .models import UploadReservation, now

    record = db.get(UploadReservation, item.id)
    db.refresh(record, with_for_update=True)
    if record.state != "pending" or record.expires_at <= now():
        raise HTTPException(
            409,
            "Upload reservation expired or was recovered; retry using the retained local recording",
        )
    usage = actor_usage(db, item.actor)
    os.replace(storage_path(item.path), destination)
    item._renamed = True
    if os.name != "nt":
        descriptor = os.open(str(Path(destination).parent), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    record.path, record.sha256, record.state = storage_key(destination), digest, "committed"
    usage.reserved_bytes -= record.size_bytes
    usage.used_bytes += size


def abort_reservation(db, item):
    from .models import UploadReservation

    db.rollback()
    row = db.get(UploadReservation, item.id)
    # A missing temp file may mean rename succeeded before a DB failure. Keep
    # that durable reservation for explicit reconciliation instead of discarding it.
    if row and row.state == "pending" and not getattr(item, "_renamed", False):
        storage_path(row.path).unlink(missing_ok=True)
        usage = actor_usage(db, row.actor)
        usage.reserved_bytes = max(0, usage.reserved_bytes - row.size_bytes)
        row.state = "failed"
        db.commit()
