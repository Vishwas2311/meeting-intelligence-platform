import asyncio
import logging
import os
import signal
import socket
import threading
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .database import SessionLocal, initialize_database
from .models import (
    ActorUsage,
    Job,
    Meeting,
    WorkerState,
    now,
)
from .providers import ProviderError, llm_mom, retry_delay
from .recovery import (
    recover_transcript,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("meeting-worker")
WORKER_ID = f"{get_settings().worker_id or 'worker'}-{socket.gethostname()[:32]}-{os.getpid()}-{uuid4().hex[:12]}"
DRAINING = False
ACTIVE_JOB = None


def acquire_job():
    with SessionLocal() as db:
        at = now()
        eligible = or_(
            and_(
                Job.status == "pending",
                or_(Job.run_after.is_(None), Job.run_after <= at, Job.deadline_at <= at),
            ),
            and_(Job.status == "running", or_(Job.lease_until.is_(None), Job.lease_until <= at)),
        )
        # Conditional UPDATE below is the atomic claim. Oracle forbids FETCH + FOR UPDATE.
        q = (
            select(Job)
            .outerjoin(ActorUsage, ActorUsage.actor == Job.owner_actor)
            .where(eligible)
            .order_by(
                func.coalesce(ActorUsage.last_dispatched, Job.created_at), Job.created_at, Job.id
            )
            .limit(1)
        )
        job = db.scalar(q)
        if not job:
            return None
        if job.attempts >= 100 or job.deadline_at and job.deadline_at <= at:
            job.status, job.error, job.completed_at = (
                "failed",
                "Processing deadline or attempt limit reached; review before retrying.",
                at,
            )
            job.lease_until = None
            db.commit()
            return None
        claim = str(uuid4())
        changed = db.execute(
            update(Job)
            .where(Job.id == job.id, eligible)
            .execution_options(synchronize_session=False)
            .values(
                status="running",
                attempts=Job.attempts + 1,
                claim_token=claim,
                lease_until=at + timedelta(minutes=5),
                run_after=None,
                worker_id=WORKER_ID,
                deadline_at=job.deadline_at
                or at + timedelta(seconds=get_settings().job_deadline_seconds),
            )
        )
        if changed.rowcount == 1 and job.owner_actor:
            from .audio_store import actor_usage

            actor_usage(db, job.owner_actor).last_dispatched = at
        db.commit()
        if changed.rowcount != 1:
            return None
        return (job.id, job.kind, job.meeting_id, claim)


async def execute(kind: str, mid: str, jid: str | None = None, claim: str | None = None) -> None:
    if kind == "improve_transcript":
        from .improvements import improve_transcript

        await improve_transcript(mid, jid, claim)
        return
    if kind == "transcribe_recap":
        from .recaps import transcribe_recap

        await transcribe_recap(mid, jid, claim)
        return
    if kind == "recover_transcript":
        await recover_transcript(mid, jid, claim)
        return
    if kind != "generate_mom":
        raise RuntimeError("Unknown job type")
    from .minutes_workflow import generate_minutes

    await generate_minutes(mid, jid, claim, sessions=SessionLocal, provider=llm_mom)


async def heartbeat(jid: str, claim: str):
    while True:
        await asyncio.sleep(30)
        with SessionLocal() as db:
            changed = db.execute(
                update(Job)
                .where(Job.id == jid, Job.claim_token == claim, Job.status == "running")
                .values(lease_until=now() + timedelta(minutes=5))
            )
            db.commit()
            if changed.rowcount != 1:
                raise RuntimeError("Job lease changed; this worker stopped safely")


async def run_job(jid: str, kind: str, mid: str, claim: str):
    global ACTIVE_JOB
    from .observability import event

    with SessionLocal() as db:
        item = db.get(Job, jid)
        if not item or item.claim_token != claim or item.status != "running":
            return
        remaining = (
            max(0.01, (item.deadline_at - now()).total_seconds())
            if item.deadline_at
            else get_settings().job_deadline_seconds
        )
    ACTIVE_JOB = jid
    event("job.started", job_id=jid, worker_id=WORKER_ID)
    renew = asyncio.create_task(heartbeat(jid, claim))
    work = None
    try:
        from .admission import current_job

        context_token = current_job.set(jid)
        try:
            with SessionLocal() as db:
                queued = db.get(Job, jid)
                evidence = db.get(Meeting, mid).evidence_revision
                if (
                    queued.input_evidence_revision is not None
                    and queued.input_evidence_revision != evidence
                ):
                    raise RuntimeError(
                        "Meeting evidence changed after queuing; submit work from the latest version"
                    )
            work = asyncio.create_task(
                asyncio.wait_for(execute(kind, mid, jid, claim), timeout=remaining)
            )
            done, _ = await asyncio.wait({work, renew}, return_when=asyncio.FIRST_COMPLETED)
            if renew in done:
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
                await renew
            await work
        finally:
            current_job.reset(context_token)
        with SessionLocal() as db:
            db.execute(
                update(Job)
                .where(Job.id == jid, Job.claim_token == claim, Job.status == "running")
                .values(
                    status="completed",
                    error=None,
                    completed_at=now(),
                    lease_until=None,
                    run_after=None,
                )
            )
            db.commit()
            event("job.completed", job_id=jid, worker_id=WORKER_ID, outcome="completed")
    except Exception as exc:
        # Exception text from HTTP libraries can contain private endpoint URLs. Keep it out of UI/logs.
        log.warning("Job %s failed (%s)", jid, type(exc).__name__)
        with SessionLocal() as db:
            item = db.get(Job, jid)
            if not item or item.claim_token != claim or item.status == "completed":
                return
            checkpoint = dict(item.checkpoint or {})
            failed_attempts = checkpoint.get("retry_count", 0) + 1
            checkpoint["retry_count"] = failed_attempts
            checkpoint["max_attempts"] = exc.max_attempts if isinstance(exc, ProviderError) else 1
            item.checkpoint = checkpoint
            item.retry_attempts = failed_attempts
            item.max_attempts = checkpoint["max_attempts"]
            retry = (
                isinstance(exc, ProviderError)
                and exc.retryable
                and failed_attempts < exc.max_attempts
                and item.attempts < 100
                and (not item.deadline_at or item.deadline_at > now())
            )
            delay = (
                exc.retry_after
                if isinstance(exc, ProviderError) and exc.retry_after is not None
                else retry_delay({}, failed_attempts)
            )
            if item.deadline_at and now() + timedelta(seconds=delay) >= item.deadline_at:
                retry = False
            item.error = (
                str(exc)[:500]
                if isinstance(exc, (ProviderError, RuntimeError))
                else "Minutes generation failed. Check the worker and configuration."
            )
            item.status = "pending" if retry else "failed"
            item.run_after = now() + timedelta(seconds=(delay)) if retry else None
            item.lease_until = None
            m = db.get(Meeting, mid)
            if m:
                m.status = "processing" if retry else "stopped"
                m.revision += 1
            db.commit()
            event(
                "job.retry" if retry else "job.failed",
                job_id=jid,
                worker_id=WORKER_ID,
                error_type=type(exc).__name__,
            )
    finally:
        ACTIVE_JOB = None
        if work is not None and not work.done():
            work.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await work
        renew.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await renew


async def main():
    global DRAINING
    DRAINING = False
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()

    def drain(*_):
        global DRAINING
        DRAINING = True
        loop.call_later(get_settings().shutdown_grace_seconds, task.cancel)

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, drain)
    initialize_database(runtime=True)
    log.info("Worker started (%s)", WORKER_ID)
    pulse = asyncio.create_task(worker_heartbeat())
    counters = asyncio.create_task(counter_maintenance())
    try:
        await work_loop()
    finally:
        counters.cancel()
        with suppress(asyncio.CancelledError):
            await counters
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse


async def counter_maintenance():
    from .reconciliation import cleanup_rate_buckets

    while not DRAINING:
        try:
            await asyncio.to_thread(cleanup_rate_buckets)
        except Exception as error:
            log.warning("Counter cleanup deferred (%s)", type(error).__name__)
        await asyncio.sleep(30)


async def worker_heartbeat():
    while True:
        try:
            with SessionLocal() as db:
                db.merge(
                    WorkerState(
                        id=WORKER_ID,
                        seen_at=now(),
                        state="draining" if DRAINING else "running",
                        active_job=ACTIVE_JOB,
                    )
                )
                db.commit()
        except OperationalError:
            log.warning("Worker heartbeat delayed by database availability")
        await asyncio.sleep(10)


async def work_loop():
    maintenance_at = 0
    while not DRAINING:
        try:
            if asyncio.get_running_loop().time() >= maintenance_at:
                from .storage import expire_audio

                try:
                    from .reconciliation import reconcile_uploads

                    await asyncio.to_thread(reconcile_uploads, True)
                    await asyncio.to_thread(expire_audio)
                except Exception as error:
                    log.warning("Maintenance deferred (%s)", type(error).__name__)
                maintenance_at = asyncio.get_running_loop().time() + 3600
            job = acquire_job()
            if job:
                await run_job(*job)
            else:
                await asyncio.sleep(2)
        except OperationalError:
            log.warning(
                "Database temporarily unavailable; worker will retry without exposing connection details"
            )
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
