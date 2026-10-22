"""Expiring leases prevent concurrent destructive maintenance on the shared database."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError


@contextmanager
def lease(task, seconds, session_factory=None):
    from .database import SessionLocal
    from .models import MaintenanceLease, now

    factory = session_factory or SessionLocal
    token = str(uuid4())
    with factory() as db:
        row = db.get(MaintenanceLease, task)
        if row is None:
            try:
                with db.begin_nested():
                    db.add(MaintenanceLease(task=task, claim_token=None, expires_at=None))
                    db.flush()
            except IntegrityError:
                pass
        result = db.execute(
            update(MaintenanceLease)
            .where(
                MaintenanceLease.task == task,
                or_(MaintenanceLease.expires_at.is_(None), MaintenanceLease.expires_at <= now()),
            )
            .values(claim_token=token, expires_at=now() + timedelta(seconds=seconds))
        )
        db.commit()
        acquired = result.rowcount == 1
    try:
        yield acquired
    finally:
        if acquired:
            with factory() as db:
                db.execute(
                    update(MaintenanceLease)
                    .where(MaintenanceLease.task == task, MaintenanceLease.claim_token == token)
                    .values(claim_token=None, expires_at=None)
                )
                db.commit()
