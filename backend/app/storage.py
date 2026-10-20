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

from sqlalchemy import select
from sqlalchemy.engine import make_url

from .config import get_settings


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def backup(destination: Path, database_url=None):
    url = make_url(database_url or get_settings().database_url)
    if url.get_backend_name() != "sqlite":
        raise RuntimeError("Use pg_dump and a coordinated audio-volume backup for PostgreSQL")
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
                    "SELECT id, meeting_id, sequence, path, sha256 FROM audio_chunks"
                ).fetchall():
                    name = f"audio/{mid}/{sequence:09d}.bin"
                    if not Path(path).is_file() or digest(path) != expected:
                        raise RuntimeError(
                            "Audio is missing or has a checksum mismatch; backup was not verified"
                        )
                    archive.write(path, name)
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
                for cid, path in db.execute("SELECT id,path FROM audio_chunks").fetchall():
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
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        sizes = list(db.scalars(select(AudioChunk.size_bytes)))
        meetings = len(list(db.scalars(select(Meeting.id))))
    free = shutil.disk_usage(settings.storage_dir).free
    return {
        "meetings": meetings,
        "audio_bytes": sum(sizes),
        "disk_free_bytes": free,
        "low_disk": free < 1024**3,
        "database": engine.dialect.name,
        "audio_retention_enabled": settings.audio_retention_enabled,
        "audio_retention_days": settings.audio_retention_days,
        "history_policy": "All transcripts and minutes retained; latest 10 complete browser copies.",
    }


def expire_audio():
    from .database import SessionLocal
    from .models import AudioChunk, AuditEvent, Job, Meeting, Mom, now
    from .recovery import has_transcript_loss

    settings = get_settings()
    if not settings.audio_retention_enabled:
        return 0
    removed = 0
    root = settings.storage_dir.resolve()
    with SessionLocal() as db:
        protected = set(
            db.scalars(select(Meeting.id).order_by(Meeting.created_at.desc()).limit(10))
        )
        eligible = db.scalars(
            select(Meeting).where(
                Meeting.ended_at < now() - timedelta(days=settings.audio_retention_days),
                Meeting.status.in_(["stopped", "ready_review"]),
            )
        ).all()
        for meeting in eligible:
            if (
                meeting.id in protected
                or has_transcript_loss(db, meeting.id)
                or db.scalar(
                    select(Job.id).where(
                        Job.meeting_id == meeting.id, Job.status.in_(["pending", "running"])
                    )
                )
            ):
                continue
            from .recovery import audio_coverage

            coverage = audio_coverage(db, meeting.id)
            mom = db.get(Mom, meeting.id)
            if (
                not mom
                or mom.status != "approved"
                or not coverage["manifest"]
                or not coverage["audio_capture_claimed_complete"]
            ):
                continue
            for chunk in db.scalars(
                select(AudioChunk).where(AudioChunk.meeting_id == meeting.id)
            ).all():
                path = Path(chunk.path).resolve()
                if not path.is_relative_to(root):
                    continue
                # Commit the intended deletion first; a crash leaves a trace and safe retry.
                db.add(
                    AuditEvent(
                        meeting_id=meeting.id,
                        actor="system",
                        action="audio.retention",
                        detail={"sequence": chunk.sequence},
                    )
                )
                db.commit()
                path.unlink(missing_ok=True)
                db.delete(chunk)
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
