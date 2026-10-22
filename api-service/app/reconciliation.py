"""Recover expired uploads conservatively; preserve unacknowledged bytes for review."""

from sqlalchemy import select


def reconcile_uploads(apply=False):
    from .audio_store import actor_usage, check_volume, storage_path
    from .config import get_settings
    from .database import SessionLocal
    from .models import AudioChunk, AuditEvent, UploadReservation, now
    from .mutations import lock_meeting

    settings = get_settings()
    check_volume(settings)
    root = settings.storage_dir.resolve()
    counts = {"expired": 0, "quarantined": 0, "released": 0}
    with SessionLocal() as db:
        ids = db.scalars(
            select(UploadReservation.id)
            .where(UploadReservation.state == "pending", UploadReservation.expires_at <= now())
            .limit(500)
        ).all()
        for rid in ids:
            item = db.get(UploadReservation, rid)
            lock_meeting(db, item.meeting_id)
            db.refresh(item)
            if item.state != "pending" or item.expires_at > now():
                db.rollback()
                continue
            counts["expired"] += 1
            temporary = storage_path(item.path, settings)
            destination = (
                root
                / item.meeting_id
                / ("recap-audio.bin" if item.slot == "recap" else f"{int(item.slot):09d}.bin")
            )
            if not temporary.is_relative_to(root) or not destination.resolve().is_relative_to(root):
                raise RuntimeError("Upload reservation path is outside the audio volume")
            committed = db.scalar(
                select(AudioChunk.id).where(
                    AudioChunk.meeting_id == item.meeting_id,
                    AudioChunk.sequence == (0 if item.slot == "recap" else int(item.slot)),
                )
            )
            candidates = [temporary] + ([] if committed else [destination])
            for candidate in candidates:
                if candidate.exists():
                    counts["quarantined"] += 1
                    if apply:
                        quarantine = root / ".quarantine"
                        quarantine.mkdir(exist_ok=True)
                        candidate.replace(quarantine / f"{item.id}-{candidate.name}")
            if apply:
                usage = actor_usage(db, item.actor)
                usage.reserved_bytes = max(0, usage.reserved_bytes - item.size_bytes)
                item.state = "reconciled"
                db.add(
                    AuditEvent(
                        meeting_id=item.meeting_id,
                        actor="system",
                        action="audio.upload_reconciled",
                        detail={"reservation_id": item.id},
                    )
                )
                db.commit()
                counts["released"] += 1
            else:
                db.rollback()
        if apply:
            cleanup_rate_buckets(max_batches=1)
    return counts


def cleanup_rate_buckets(*, max_batches=8, time_budget=2):
    """Short committed batches; independent of hourly audio maintenance and long jobs."""
    import time

    from sqlalchemy import and_, delete, or_

    from .database import SessionLocal
    from .models import RateBucket, now

    began, removed = time.monotonic(), 0
    for _ in range(max_batches):
        if time.monotonic() - began >= time_budget:
            break
        with SessionLocal() as db:
            at = now()
            rows = db.execute(
                select(RateBucket.actor, RateBucket.operation, RateBucket.window)
                .where(RateBucket.expires_at < at)
                .order_by(RateBucket.expires_at)
                .limit(250)
            ).all()
            if not rows:
                break
            removed += db.execute(
                delete(RateBucket)
                .execution_options(synchronize_session=False)
                .where(
                    RateBucket.expires_at < at,
                    or_(
                        *(
                            and_(
                                RateBucket.actor == row.actor,
                                RateBucket.operation == row.operation,
                                RateBucket.window == row.window,
                            )
                            for row in rows
                        )
                    ),
                )
            ).rowcount
            db.commit()
    return removed
