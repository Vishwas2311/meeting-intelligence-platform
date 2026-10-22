"""Atomic per-actor admission shared by all API processes through the database."""

from contextlib import nullcontext
from datetime import UTC, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError


def consume(actor, operation, maximum, seconds=60, session=None):
    from .database import SessionLocal
    from .models import RateBucket

    for attempt in range(3):
        try:
            with nullcontext(session) if session is not None else SessionLocal() as db:
                at = db.scalar(select(func.current_timestamp()))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=UTC)
                window = int(at.timestamp()) // seconds
                key = (actor, operation, window)
                result = db.execute(
                    update(RateBucket)
                    .where(
                        RateBucket.actor == actor,
                        RateBucket.operation == operation,
                        RateBucket.window == window,
                        RateBucket.count < maximum,
                    )
                    .values(count=RateBucket.count + 1)
                )
                if result.rowcount == 0:
                    if db.get(RateBucket, key):
                        retry = seconds - int(at.timestamp()) % seconds
                        raise HTTPException(
                            429,
                            "Request limit reached; retry later",
                            headers={"Retry-After": str(retry)},
                        )
                    db.add(
                        RateBucket(
                            actor=actor,
                            operation=operation,
                            window=window,
                            count=1,
                            expires_at=at + timedelta(seconds=seconds * 2),
                        )
                    )
                if session is None:
                    db.commit()
                return
        except IntegrityError:
            if session is not None:
                raise
            if attempt == 2:
                raise HTTPException(503, "Admission temporarily unavailable") from None


def admit_request(actor, path, method, settings):
    consume(actor, "requests", settings.actor_requests_per_minute)
    if method == "POST" and (path.endswith("/audio-chunks") or path.endswith("/audio")):
        consume(actor, "uploads", settings.actor_uploads_per_minute)
    if path == "/api/speech/token":
        consume(actor, "speech_tokens", settings.actor_speech_tokens_per_minute)


def admit_job(db, meeting):
    from .audio_store import actor_usage
    from .config import get_settings
    from .models import Job

    actor_usage(
        db, meeting.created_by
    )  # Serialize concurrent submissions across this owner's meetings.
    count = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.owner_actor == meeting.created_by, Job.status.in_(["pending", "running"]))
    )
    if count >= get_settings().actor_pending_jobs:
        raise HTTPException(
            429, "Processing queue allowance reached", headers={"Retry-After": "30"}
        )
    consume(meeting.created_by, "jobs", get_settings().actor_jobs_per_hour, 3600, session=db)
