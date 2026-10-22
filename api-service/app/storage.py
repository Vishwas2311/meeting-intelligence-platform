"""Consistent SQLite/audio archives. Restore only into an empty, explicitly chosen folder."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from .audio_store import storage_path
from .config import get_settings


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def backup(destination: Path, database_url=None):
    from sqlalchemy.orm import sessionmaker

    from .database import make_engine
    from .maintenance import lease

    url = database_url or get_settings().database_url
    if make_url(url).get_backend_name() != "sqlite":
        raise RuntimeError(
            "In-app backup is SQLite-only; use coordinated Oracle RMAN and audio-volume backup"
        )
    target = make_engine(url)
    try:
        with lease("data-maintenance", 3600, sessionmaker(bind=target)) as held:
            if not held:
                raise RuntimeError("Storage maintenance is active; retry backup later")
            return _backup(destination, url)
    finally:
        target.dispose()


def _backup(destination: Path, database_url=None):
    url = make_url(database_url or get_settings().database_url)
    if url.get_backend_name() != "sqlite":
        raise RuntimeError(
            "In-app backup is SQLite-only. Use DBA-managed Oracle RMAN/Data Pump "
            "or PostgreSQL pg_dump with a coordinated audio-storage backup."
        )
    if destination.exists():
        raise RuntimeError("Backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="meeting-backup-") as folder:
        snapshot = Path(folder) / "meeting_intelligence.db"
        with (
            closing(sqlite3.connect(url.database)) as source,
            closing(sqlite3.connect(snapshot)) as db,
        ):
            source.backup(db)
            if (
                db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise RuntimeError("Database integrity check failed; original data was kept")
            files = {}
            with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
                for cid, mid, sequence, path, expected in db.execute(
                    "SELECT id, meeting_id, sequence, path, sha256 FROM audio_chunks WHERE storage_state='committed'"
                ).fetchall():
                    name = f"audio/{mid}/{sequence:09d}.bin"
                    actual_path = storage_path(path)
                    if not actual_path.is_file() or digest(actual_path) != expected:
                        raise RuntimeError(
                            "Audio is missing or has a checksum mismatch; backup was not verified"
                        )
                    archive.write(actual_path, name)
                    files[name] = expected
                    db.execute("UPDATE audio_chunks SET path=? WHERE id=?", (name, cid))
                db.commit()
                archive.write(snapshot, "meeting_intelligence.db")
                files["meeting_intelligence.db"] = digest(snapshot)
                archive.writestr("manifest.json", json.dumps({"version": 1, "files": files}))
    return destination


def restore(archive_path: Path, destination: Path):
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(
            "Restore requires an empty destination; stop the app and choose a new folder"
        )
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("version") != 1 or set(archive.namelist()) != set(manifest["files"]) | {
            "manifest.json"
        }:
            raise RuntimeError("Invalid backup manifest")
        if sum(f.file_size for f in archive.infolist()) > 20 * 1024**3:
            raise RuntimeError("Backup exceeds the 20 GB restore limit")
        for name in manifest["files"]:
            target = (destination / name).resolve()
            if not target.is_relative_to(destination) or name.startswith(("/", "\\")):
                raise RuntimeError("Unsafe backup path")
        # Verify everything before creating a usable database in the destination.
        with tempfile.TemporaryDirectory(prefix="meeting-restore-") as folder:
            staging = Path(folder)
            for name, expected in manifest["files"].items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if digest(target) != expected:
                    raise RuntimeError("Backup checksum failed; nothing was restored")
            with closing(sqlite3.connect(staging / "meeting_intelligence.db")) as db:
                if (
                    db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or db.execute("PRAGMA foreign_key_check").fetchall()
                ):
                    raise RuntimeError("Backup database integrity failed")
                for cid, path in db.execute(
                    "SELECT id,path FROM audio_chunks WHERE storage_state='committed'"
                ).fetchall():
                    if path not in manifest["files"]:
                        raise RuntimeError("Backup is missing a referenced audio file")
                    db.execute(
                        "UPDATE audio_chunks SET path=? WHERE id=?", (str(destination / path), cid)
                    )
                db.commit()
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staging, destination, dirs_exist_ok=True)
    return destination


def storage_status():
    from .database import SessionLocal, engine
    from .models import AudioChunk, Meeting

    settings = get_settings()
    from .audio_store import check_volume

    check_volume(settings)
    with SessionLocal() as db:
        total = db.scalar(
            select(func.coalesce(func.sum(AudioChunk.size_bytes), 0)).where(
                AudioChunk.storage_state != "retired"
            )
        )
        meetings = db.scalar(select(func.count()).select_from(Meeting))
    free = shutil.disk_usage(settings.storage_dir).free
    return {
        "meetings": meetings,
        "audio_bytes": total,
        "disk_free_bytes": free,
        "low_disk": free < 1024**3,
        "database": engine.dialect.name,
        "backup_supported": engine.dialect.name == "sqlite",
        "backup_guidance": "In-app verified backup"
        if engine.dialect.name == "sqlite"
        else "Use DBA-managed database backup and coordinated audio-storage backup.",
        "audio_retention_enabled": settings.audio_retention_enabled,
        "audio_retention_days": settings.audio_retention_days,
        "history_policy": "All transcripts and minutes retained; latest 10 complete browser copies.",
    }


def expire_audio():
    from .audio_store import actor_usage, check_volume
    from .database import SessionLocal
    from .maintenance import lease
    from .models import AudioChunk, AuditEvent, Job, Meeting, Mom, now
    from .mutations import lock_meeting
    from .recovery import audio_coverage, has_transcript_loss

    settings = get_settings()
    if not settings.audio_retention_enabled:
        return 0
    check_volume(settings)
    removed = 0
    root = settings.storage_dir.resolve()
    with lease("data-maintenance", 1800) as held:
        if not held:
            return 0
        with SessionLocal() as db:
            # Finish previously committed deletion intentions before reevaluating coverage.
            retiring = db.scalars(
                select(AudioChunk.id).where(AudioChunk.storage_state == "retiring").limit(5000)
            ).all()
            protected = set(
                db.scalars(select(Meeting.id).order_by(Meeting.created_at.desc()).limit(10))
            )
            eligible = db.scalars(
                select(Meeting.id)
                .join(Mom, Mom.meeting_id == Meeting.id)
                .where(
                    Mom.status == "approved",
                    Meeting.id.not_in(protected),
                    ~select(Job.id)
                    .where(Job.meeting_id == Meeting.id, Job.status.in_(["pending", "running"]))
                    .exists(),
                    Meeting.ended_at < now() - timedelta(days=settings.audio_retention_days),
                    Meeting.status.in_(["stopped", "ready_review"]),
                )
                .order_by(Meeting.ended_at)
                .limit(100)
            ).all()
            for mid in eligible:
                meeting = lock_meeting(db, mid)
                if (
                    mid in protected
                    or has_transcript_loss(db, mid)
                    or db.scalar(
                        select(Job.id).where(
                            Job.meeting_id == mid, Job.status.in_(["pending", "running"])
                        )
                    )
                ):
                    db.rollback()
                    continue
                mom = db.get(Mom, mid)
                coverage = audio_coverage(db, mid)
                if (
                    not mom
                    or mom.status != "approved"
                    or not coverage["manifest"]
                    or not coverage["audio_capture_claimed_complete"]
                ):
                    db.rollback()
                    continue
                for chunk in db.scalars(
                    select(AudioChunk).where(
                        AudioChunk.meeting_id == mid, AudioChunk.storage_state == "committed"
                    )
                ):
                    chunk.storage_state = "retiring"
                    retiring.append(chunk.id)
                    db.add(
                        AuditEvent(
                            meeting_id=mid,
                            actor="system",
                            action="audio.retention",
                            detail={
                                "sequence": chunk.sequence,
                                "policy": settings.retention_policy_version or "local-opt-in",
                            },
                        )
                    )
                db.commit()
            for cid in dict.fromkeys(retiring):
                chunk = db.get(AudioChunk, cid)
                if not chunk or chunk.storage_state != "retiring":
                    continue
                meeting = lock_meeting(db, chunk.meeting_id)
                db.refresh(chunk)
                if chunk.storage_state != "retiring":
                    db.rollback()
                    continue
                path = storage_path(chunk.path)
                if not path.is_relative_to(root):
                    raise RuntimeError("Audio retirement path is outside the mounted volume")
                path.unlink(missing_ok=True)
                usage = actor_usage(db, meeting.created_by)
                usage.used_bytes = max(0, usage.used_bytes - chunk.size_bytes)
                chunk.storage_state, chunk.retired_at = "retired", now()
                db.commit()
                removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("backup")
    create.add_argument("destination", type=Path)
    recover = sub.add_parser("restore")
    recover.add_argument("archive", type=Path)
    recover.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = (
        backup(args.destination)
        if args.command == "backup"
        else restore(args.archive, args.destination)
    )
    print(result)


if __name__ == "__main__":
    main()
