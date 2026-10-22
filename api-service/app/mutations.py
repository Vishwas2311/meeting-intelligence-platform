"""One lock order for all meeting state/evidence mutations."""

from fastapi import HTTPException
from sqlalchemy import select


def lock_meeting(db, mid, actor=None):
    from .models import Meeting

    if db.bind.dialect.name == "sqlite":
        # sqlite3 legacy transaction mode does not begin a transaction for SELECT.
        if not db.connection().connection.driver_connection.in_transaction:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    statement = select(Meeting).where(Meeting.id == mid).execution_options(populate_existing=True)
    if db.bind.dialect.name != "sqlite":
        statement = statement.with_for_update()
    meeting = db.scalar(statement)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if actor is not None and meeting.created_by != actor:
        raise HTTPException(403, "Meeting access denied")
    return meeting


def check_revision(mom, expected):
    from .config import get_settings

    if expected is None:
        if get_settings().app_env == "production":
            raise HTTPException(422, "expected_revision is required")
        # shortcut: legacy local/test clients may omit a revision; production never may.
        return
    if expected != mom.revision:
        raise HTTPException(409, {"code": "revision_conflict", "current_revision": mom.revision})


def changed_evidence(meeting):
    meeting.evidence_revision += 1
    meeting.revision += 1


def finish_job(db, jid, claim):
    """Commit business effects and job completion using the caller's transaction."""
    from .models import Job, now

    if not jid:
        return
    job = db.scalar(select(Job).where(Job.id == jid).with_for_update())
    if not job or job.claim_token != claim or job.status != "running":
        raise RuntimeError("Job lease changed; this worker stopped safely")
    if job.lease_until and job.lease_until <= now():
        raise RuntimeError("Job lease expired; this worker stopped safely")
    if job.deadline_at and job.deadline_at <= now():
        raise RuntimeError("Job deadline exceeded; this worker stopped safely")
    job.effect_committed_at = now()
    job.completed_at = now()
    job.status = "completed"
    job.lease_until = job.run_after = None
    job.error = None
