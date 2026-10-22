"""Opt-in real Oracle admission and edit/approval race; never uses DATABASE_URL implicitly."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import sessionmaker


@pytest.mark.skipif(
    not os.getenv("ORACLE_TEST_URL") or os.getenv("ORACLE_TEST_SCHEMA_CONFIRMED") != "true",
    reason="Dedicated Oracle test schema and explicit confirmation required",
)
def test_real_oracle_shared_counters_and_atomic_approval(client, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from app import database, models
    from app.database import check_schema_version, make_engine
    from app.limits import consume
    from app.routes.minutes import approve, edit_mom
    from app.schemas import Approval, MomEdit

    engine = make_engine(os.environ["ORACLE_TEST_URL"])
    assert engine.dialect.name == "oracle"
    if not inspect(engine).get_table_names():
        with engine.begin() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    check_schema_version(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    actor = "oracle-test-" + uuid4().hex
    mid = str(uuid4())

    def admitted(_):
        try:
            consume(actor, "race", 5)
            return 200
        except HTTPException as error:
            return error.status_code

    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(pool.map(admitted, range(20)))
        # Real database time can cross a window boundary during the concurrent calls.
        assert set(statuses) <= {200, 429}
        with sessions() as db:
            counts = db.scalars(
                select(models.RateBucket.count).where(
                    models.RateBucket.actor == actor, models.RateBucket.operation == "race"
                )
            ).all()
        assert counts and all(0 < count <= 5 for count in counts)
        assert sum(counts) == statuses.count(200)
        with sessions() as db:
            db.add(
                models.Meeting(
                    id=mid, title="Synthetic Oracle race", created_by=actor, status="stopped"
                )
            )
            db.flush()
            db.add(
                models.Mom(
                    meeting_id=mid, data={"title": "Synthetic", "summary": "Before"}, status="draft"
                )
            )
            db.commit()

        def mutate(edit):
            with sessions() as db:
                try:
                    if edit:
                        edit_mom(
                            mid,
                            MomEdit(title="Synthetic", summary="After", expected_revision=0),
                            db,
                            actor,
                        )
                    else:
                        approve(mid, Approval(approved=True, expected_revision=0), db, actor)
                    return 200
                except HTTPException as error:
                    return error.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(mutate, [True, False]))
        assert sorted(results) == [200, 409]
        with sessions() as db:
            mom = db.get(models.Mom, mid)
            if mom.status == "approved":
                assert mom.data["summary"] == "Before"
                assert db.get(models.MomRevision, mom.approved_revision_id).data == mom.data
    finally:
        with engine.begin() as connection:
            connection.execute(delete(models.AuditEvent).where(models.AuditEvent.meeting_id == mid))
            connection.execute(
                delete(models.MomRevision).where(models.MomRevision.meeting_id == mid)
            )
            connection.execute(delete(models.Mom).where(models.Mom.meeting_id == mid))
            connection.execute(delete(models.Meeting).where(models.Meeting.id == mid))
            connection.execute(delete(models.RateBucket).where(models.RateBucket.actor == actor))
        engine.dispose()
