import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .database import SessionLocal, initialize_database
from .models import AuditEvent, Job, Meeting, Mom, MomRevision, WorkerState, now
from .providers import ProviderError, llm_mom, retry_delay
from .recovery import (
    has_transcript_loss,
    recover_transcript,
    require_complete_audio,
    transcript_segments,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("meeting-worker")


def acquire_job():
    with SessionLocal() as db:
        at = now()
        eligible = or_(
            and_(Job.status == "pending", or_(Job.run_after.is_(None), Job.run_after <= at)),
            and_(Job.status == "running", or_(Job.lease_until.is_(None), Job.lease_until <= at)),
        )
        q = select(Job).where(eligible).order_by(Job.created_at).limit(1)
        if not db.bind.dialect.name == "sqlite":
            q = q.with_for_update(skip_locked=True)
        job = db.scalar(q)
        if not job:
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
            )
        )
        db.commit()
        if changed.rowcount != 1:
            return None
        return (job.id, job.kind, job.meeting_id, claim)


async def execute(kind: str, mid: str, jid: str | None = None, claim: str | None = None) -> None:
    if kind == "recover_transcript":
        await recover_transcript(mid, jid, claim)
        return
    if kind != "generate_mom":
        raise RuntimeError("Unknown job type")
    with SessionLocal() as db:
        m = db.get(Meeting, mid)
        if not m:
            raise RuntimeError("Meeting not found")
        existing = db.get(Mom, mid)
        if existing and existing.status == "approved":
            raise RuntimeError("Approved MoM cannot be overwritten")
        segs = transcript_segments(db, mid)
        require_complete_audio(db, mid)
        if not segs:
            raise RuntimeError("No finalized transcript segments. Cannot create MoM.")
        text = "\n".join(
            f"[{s.id} {s.start_ms}-{s.end_ms}ms {s.speaker_id or 'unknown'}] {s.text}" for s in segs
        )
        # Completeness cannot be inferred from segments alone. Production needs capture/gap reconciliation.
        if (
            m.capture_status == "lost"
            or m.sync_status in ("offline", "failed")
            or has_transcript_loss(db, mid)
        ):
            raise RuntimeError(
                "Capture/transcript incomplete; reconcile audio and gaps before generating MoM"
            )
        title = m.title
    settings = get_settings()
    limit = settings.llm_max_input_chars
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                title,
                text,
                settings.llm_endpoint,
                settings.llm_model,
                limit,
                settings.llm_response_format,
            ]
        ).encode()
    ).hexdigest()
    checkpoint = {"fingerprint": fingerprint, "sections": {}}
    if jid:
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if job.checkpoint and job.checkpoint.get("fingerprint") == fingerprint:
                checkpoint = job.checkpoint

    async def summarize(key, content, ids, section_title):
        if key in checkpoint["sections"]:
            return checkpoint["sections"][key]
        result = await llm_mom(settings, section_title, content, ids, [])
        checkpoint["sections"][key] = result
        checkpoint["retry_count"] = 0
        if jid:
            with SessionLocal() as db:
                changed = db.execute(
                    update(Job)
                    .where(Job.id == jid, Job.status == "running", Job.claim_token == claim)
                    .values(checkpoint=json.loads(json.dumps(checkpoint)))
                )
                db.commit()
                if changed.rowcount != 1:
                    raise RuntimeError("Job lease changed; this worker stopped safely")
        await asyncio.sleep(settings.llm_request_interval_seconds)
        return result

    # Keep successful section results across retries and worker restarts.
    if len(text) <= limit:
        result = await summarize("final", text, [s.id for s in segs], title)
    else:
        sections = []
        batch = []
        length = 0
        for segment in segs:
            line = f"[{segment.id} {segment.start_ms}-{segment.end_ms}ms {segment.speaker_id or 'unknown'}] {segment.text}"
            if len(line) > limit:
                raise RuntimeError("Individual transcript segment too large for summarization")
            if batch and length + len(line) > limit // 2:
                sections.append(batch)
                batch = []
                length = 0
            batch.append(segment)
            length += len(line) + 1
        if batch:
            sections.append(batch)
        summaries = []
        for i, part in enumerate(sections):
            chunk = "\n".join(f"[{s.id} {s.start_ms}-{s.end_ms}ms] {s.text}" for s in part)
            extracted = await summarize(
                str(i), chunk, [s.id for s in part], f"{title} / section {i + 1}"
            )
            summaries.append(extracted)
        # Structured intermediate summaries, not invented replacement transcript.
        summary_text = "\n".join(
            json.dumps({"section": i + 1, "extractions": v}) for i, v in enumerate(summaries)
        )
        if len(summary_text) > limit:
            # Conservative failure: avoid dropping evidence silently.
            raise RuntimeError(
                "Too many long-meeting summaries for final merge; adjust LLM context or add another summarization tier"
            )
        result = await summarize("final", summary_text, [s.id for s in segs], title)
        result["requires_review"] = True
    with SessionLocal() as db:
        if jid:
            job = db.scalar(select(Job).where(Job.id == jid).with_for_update())
            if job.claim_token != claim or job.status != "running":
                raise RuntimeError("Job lease changed; this worker stopped safely")
        if has_transcript_loss(db, mid):
            raise RuntimeError(
                "Transcription loss was reported during generation. Recover before retrying."
            )
        require_complete_audio(db, mid)
        old = db.get(Mom, mid)
        if old and old.status == "approved":
            raise RuntimeError("Approved MoM cannot be overwritten")
        if old:
            db.add(
                MomRevision(
                    meeting_id=mid,
                    data=old.data,
                    status=old.status,
                    actor="system",
                    provenance={"schema_version": 1},
                )
            )
            old.data = result
            old.status = "draft"
            old.updated_at = now()
        else:
            db.add(Mom(meeting_id=mid, data=result, status="draft"))
        db.add(
            MomRevision(
                meeting_id=mid,
                data=result,
                status="draft",
                actor="system",
                provenance={
                    "schema_version": 1,
                    "prompt_version": 2,
                    "model": settings.llm_model,
                    "transcript_fingerprint": fingerprint,
                },
            )
        )
        db.get(Meeting, mid).status = "ready_review"
        db.get(Meeting, mid).revision += 1
        db.add(
            AuditEvent(
                meeting_id=mid,
                actor="system",
                action="mom.generated",
                detail={"segments": len(segs)},
            )
        )
        db.commit()


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
                return


async def run_job(jid: str, kind: str, mid: str, claim: str):
    with SessionLocal() as db:
        item = db.get(Job, jid)
        if not item or item.claim_token != claim or item.status != "running":
            return
    renew = asyncio.create_task(heartbeat(jid, claim))
    try:
        await execute(kind, mid, jid, claim)
        with SessionLocal() as db:
            db.execute(
                update(Job)
                .where(Job.id == jid, Job.claim_token == claim)
                .values(status="completed", error=None, lease_until=None, run_after=None)
            )
            db.commit()
    except Exception as exc:
        # Exception text from HTTP libraries can contain private endpoint URLs. Keep it out of UI/logs.
        log.warning("Job %s failed (%s)", jid, type(exc).__name__)
        with SessionLocal() as db:
            item = db.get(Job, jid)
            if item.claim_token != claim:
                return
            checkpoint = dict(item.checkpoint or {})
            failed_attempts = checkpoint.get("retry_count", 0) + 1
            checkpoint["retry_count"] = failed_attempts
            item.checkpoint = checkpoint
            retry = (
                isinstance(exc, ProviderError)
                and exc.retryable
                and failed_attempts < exc.max_attempts
            )
            item.error = (
                str(exc)[:500]
                if isinstance(exc, (ProviderError, RuntimeError))
                else "Minutes generation failed. Check the worker and configuration."
            )
            item.status = "pending" if retry else "failed"
            item.run_after = (
                now()
                + timedelta(
                    seconds=(
                        exc.retry_after
                        if exc.retry_after is not None
                        else retry_delay({}, failed_attempts)
                    )
                )
                if retry
                else None
            )
            item.lease_until = None
            m = db.get(Meeting, mid)
            if m:
                m.status = "processing" if retry else "stopped"
                m.revision += 1
            db.commit()
    finally:
        renew.cancel()
        with suppress(asyncio.CancelledError):
            await renew


async def main():
    initialize_database()
    log.info("Worker started")
    pulse = asyncio.create_task(worker_heartbeat())
    try:
        await work_loop()
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse


async def worker_heartbeat():
    while True:
        try:
            with SessionLocal() as db:
                db.merge(WorkerState(id="local", seen_at=now()))
                db.commit()
        except OperationalError:
            log.warning("Worker heartbeat delayed by database availability")
        await asyncio.sleep(10)


async def work_loop():
    maintenance_at = 0
    while True:
        try:
            if asyncio.get_running_loop().time() >= maintenance_at:
                from .storage import expire_audio

                await asyncio.to_thread(expire_audio)
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
