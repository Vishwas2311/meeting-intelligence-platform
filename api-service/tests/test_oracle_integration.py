"""Run only against an explicitly approved, dedicated Oracle test schema."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import sessionmaker


@pytest.mark.skipif(
    not os.getenv("ORACLE_TEST_URL") or os.getenv("ORACLE_TEST_SCHEMA_CONFIRMED") != "true",
    reason="Dedicated Oracle schema and explicit ORACLE_TEST_SCHEMA_CONFIRMED=true required",
)
def test_real_oracle_notes_jobs_json_and_rollback(client, auth, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from app import database, worker
    from app.database import Base, check_schema_version, db_session, make_engine
    from app.main import app
    from app.models import Job, Meeting, PersonalRecap, RateBucket

    target = make_engine(os.environ["ORACLE_TEST_URL"])
    assert target.dialect.name == "oracle", "ORACLE_TEST_URL must select Oracle"
    if not inspect(target).get_table_names():
        with target.begin() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    check_schema_version(target)
    sessions = sessionmaker(bind=target, expire_on_commit=False, autoflush=False)
    with sessions() as db:
        assert db.scalar(select(Job.id).limit(1)) is None, (
            "Use a dedicated test schema without existing jobs"
        )
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    # Middleware admission must use Oracle too, not the fixture's local SQLite database.
    monkeypatch.setattr(database, "SessionLocal", sessions)

    def dependency():
        with sessions() as db:
            yield db

    app.dependency_overrides[db_session] = dependency
    mid = str(uuid4())
    blank_mid = str(uuid4())
    text = "English हिन्दी मराठी 中文. " * 3000
    try:
        assert (
            client.post(
                "/api/recaps", headers=auth, json={"id": mid, "title": "Oracle integration"}
            ).status_code
            == 201
        )
        # Oracle returns empty CLOB/VARCHAR values as None; application notes remain blank strings.
        assert client.get(f"/api/recaps/{mid}", headers=auth).json()["notes"] == ""
        assert (
            client.patch(
                f"/api/recaps/{mid}/notes",
                headers=auth,
                json={"notes": text[:19000], "reviewed": True, "revision": 0},
            ).status_code
            == 200
        )
        with sessions() as db:
            meeting = db.get(Meeting, mid)
            assert meeting.created_at.tzinfo is not None
            assert db.get(PersonalRecap, mid).notes == text[:19000]
            job = Job(meeting_id=mid, kind="generate_mom", checkpoint={"large": text})
            db.add(job)
            db.commit()
            jid = job.id
        with sessions() as db:
            assert db.get(Job, jid).checkpoint["large"] == text
            db.add(Meeting(id=blank_mid, title="Rolled back", created_at=datetime.now(UTC)))
            db.flush()
            db.rollback()
        with sessions() as db:
            assert db.get(Meeting, blank_mid) is None

        async def fake(settings, title, transcript, ids, gaps):
            return {"title": title, "summary": "Oracle integration result", "transcript_gaps": []}

        monkeypatch.setattr(worker, "llm_mom", fake)
        claim = worker.acquire_job()
        assert claim and claim[0] == jid
        assert worker.acquire_job() is None
        asyncio.run(worker.run_job(*claim))
        with sessions() as db:
            assert db.get(Job, jid).status == "completed"
        assert client.get(f"/api/meetings/{mid}/mom", headers=auth).status_code == 200
        assert client.get(f"/api/meetings/{mid}/transcript", headers=auth).status_code == 200
    finally:
        app.dependency_overrides.pop(db_session, None)
        # Leave tables and all unrelated records intact; remove only this test's UUIDs.
        with target.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                if "meeting_id" in table.c:
                    connection.execute(
                        delete(table).where(table.c.meeting_id.in_([mid, blank_mid]))
                    )
            connection.execute(delete(Meeting).where(Meeting.id.in_([mid, blank_mid])))
            connection.execute(delete(RateBucket).where(RateBucket.actor == "local-user"))
        target.dispose()
