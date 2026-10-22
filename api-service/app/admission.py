"""Shared provider permits, daily call budgets, and outage cooldowns."""

import asyncio
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import timedelta
from functools import wraps

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

current_job = ContextVar("meeting_provider_job", default=None)
usage = ContextVar("meeting_provider_usage", default=None)


def record_usage(response):
    values = usage.get()
    if values is None:
        return
    values["status"] = response.status_code
    try:
        reported = response.json().get("usage", {})
        for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
            value = reported.get(key)
            if isinstance(value, int) and 0 <= value <= 100_000_000:
                values[key] = value
    except (ValueError, TypeError, AttributeError):
        pass


def acquire(resource):
    from .config import get_settings
    from .database import SessionLocal
    from .limits import consume
    from .models import Job, Meeting, ProviderLease, ProviderState, now
    from .providers import ProviderError

    jid = current_job.get()
    if jid is None:
        return None
    settings = get_settings()
    with SessionLocal() as db:
        if db.bind.dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        state = db.scalar(
            select(ProviderState).where(ProviderState.resource == resource).with_for_update()
        )
        if state is None:
            try:
                with db.begin_nested():
                    db.add(ProviderState(resource=resource))
                    db.flush()
            except IntegrityError:
                pass
            state = db.scalar(
                select(ProviderState).where(ProviderState.resource == resource).with_for_update()
            )
        if state.cooldown_until and state.cooldown_until > now():
            raise ProviderError(
                "Provider is cooling down; work retained",
                retryable=True,
                retry_after=30,
                max_attempts=30,
            )
        db.execute(
            delete(ProviderLease).where(
                ProviderLease.resource == resource, ProviderLease.expires_at <= now()
            )
        )
        count = db.scalar(
            select(func.count())
            .select_from(ProviderLease)
            .where(ProviderLease.resource == resource)
        )
        if count >= settings.provider_max_concurrency:
            raise ProviderError(
                "Provider is busy; work retained", retryable=True, retry_after=5, max_attempts=100
            )
        job = db.get(Job, jid)
        actor = job.owner_actor or db.get(Meeting, job.meeting_id).created_by
        maximum = (
            settings.daily_llm_calls if resource == "llm" else settings.daily_transcription_calls
        )
        consume(actor, resource + "_daily", maximum, 86400, session=db)
        lease = ProviderLease(
            resource=resource, job_id=jid, expires_at=now() + timedelta(seconds=650)
        )
        db.add(lease)
        db.commit()
        return lease.token


def release(resource, token, error=None, measured=None):
    from .database import SessionLocal
    from .models import AuditEvent, Job, ProviderLease, ProviderState, now
    from .providers import ProviderError

    if token is None:
        return
    with SessionLocal() as db:
        state = db.scalar(
            select(ProviderState).where(ProviderState.resource == resource).with_for_update()
        )
        permit = db.get(ProviderLease, token)
        job = db.get(Job, permit.job_id) if permit else None
        if job:
            db.add(
                AuditEvent(
                    meeting_id=job.meeting_id,
                    actor=job.owner_actor or "system",
                    action=f"provider.{resource}.{'failed' if error else 'ok'}",
                    detail={"job_id": job.id, "permit": token, **(measured or {})},
                )
            )
        db.execute(delete(ProviderLease).where(ProviderLease.token == token))
        if isinstance(error, ProviderError) and error.retryable:
            state.cooldown_until = now() + timedelta(
                seconds=min(error.retry_after if error.retry_after is not None else 30, 900)
            )
        db.commit()


@asynccontextmanager
async def provider_permit(resource):
    token = await asyncio.to_thread(acquire, resource)
    error = None
    measured = {}
    context = usage.set(measured)
    began = time.monotonic()
    try:
        yield
    except BaseException as exc:
        error = exc
        raise
    finally:
        measured["seconds"] = round(time.monotonic() - began, 3)
        usage.reset(context)
        try:
            await asyncio.to_thread(release, resource, token, error, measured)
        except Exception as exc:
            from .observability import event

            event("provider.release_deferred", error_type=type(exc).__name__)


def metered(resource):
    def decorate(function):
        @wraps(function)
        async def invoke(*args, **kwargs):
            async with provider_permit(resource):
                return await function(*args, **kwargs)

        return invoke

    return decorate
