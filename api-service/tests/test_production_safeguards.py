"""Regression tests for data integrity and bounded production controls."""

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO

import pytest
from fastapi import HTTPException
from sqlalchemy import select


def meeting(client, auth):
    response = client.post(
        "/api/meetings",
        headers=auth,
        json={"title": "Synthetic safeguards", "consent_confirmed": True},
    )
    assert response.status_code == 201, response.text
    mid = response.json()["id"]
    assert client.post(f"/api/meetings/{mid}/start", headers=auth).status_code == 200
    return mid


def test_decorated_provider_observes_the_workers_budget_context(client, auth, monkeypatch):
    import asyncio

    from app.admission import current_job
    from app.config import get_settings
    from app.database import SessionLocal
    from app.limits import consume
    from app.models import Job
    from app.providers import llm_mom

    monkeypatch.setenv("DAILY_LLM_CALLS", "1")
    get_settings.cache_clear()
    mid = meeting(client, auth)
    with SessionLocal() as db:
        job = Job(meeting_id=mid, kind="generate_mom", status="running", owner_actor="local-user")
        db.add(job)
        db.commit()
        jid = job.id
    consume("local-user", "llm_daily", 1, 86400)
    token = current_job.set(jid)
    try:
        with pytest.raises(HTTPException) as rejected:
            asyncio.run(llm_mom(get_settings(), "Synthetic", "Not sent", [], []))
        assert rejected.value.status_code == 429
    finally:
        current_job.reset(token)


def test_stale_minutes_edits_cannot_overwrite_approved_snapshot(client, auth):
    from app.database import SessionLocal
    from app.models import Mom, MomRevision

    mid = meeting(client, auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    data = {"title": "Synthetic", "summary": "First draft"}
    with SessionLocal() as db:
        db.add(Mom(meeting_id=mid, data=data, status="draft"))
        db.commit()
    path = f"/api/meetings/{mid}/mom"
    edited = client.patch(
        path, headers=auth, json={"data": {**data, "summary": "Reviewed"}, "expected_revision": 0}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 1
    assert (
        client.post(
            path + "/approve", headers=auth, json={"expected_revision": 0, "approved": True}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/approve", headers=auth, json={"expected_revision": 1, "approved": True}
        ).status_code
        == 200
    )
    assert (
        client.patch(path, headers=auth, json={"data": data, "expected_revision": 1}).status_code
        == 409
    )
    with SessionLocal() as db:
        mom = db.get(Mom, mid)
        assert mom.data["summary"] == "Reviewed"
        assert db.get(MomRevision, mom.approved_revision_id).data == mom.data


def test_concurrent_identical_transcript_ingest_is_idempotent(client, auth):
    from app.database import SessionLocal
    from app.models import Segment

    mid = meeting(client, auth)
    body = {
        "segments": [
            {"client_event_id": "same", "text": "Synthetic only", "start_ms": 0, "end_ms": 1000}
        ]
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(f"/api/meetings/{mid}/segments", headers=auth, json=body),
                range(2),
            )
        )
    assert [r.status_code for r in responses] == [200, 200]
    with SessionLocal() as db:
        assert len(db.scalars(select(Segment).where(Segment.meeting_id == mid)).all()) == 1


def test_bad_upload_releases_reservation_and_preserves_retry(client, auth):
    from app.database import SessionLocal
    from app.models import ActorUsage, UploadReservation

    mid = meeting(client, auth)
    data = b"synthetic recording"
    fields = {"sequence": "0", "start_ms": "0", "end_ms": "1000", "sha256": "0" * 64}
    path = f"/api/meetings/{mid}/audio-chunks"
    assert (
        client.post(
            path, headers=auth, data=fields, files={"audio": ("part.webm", data, "audio/webm")}
        ).status_code
        == 422
    )
    with SessionLocal() as db:
        assert db.get(ActorUsage, "local-user").reserved_bytes == 0
        assert db.scalar(select(UploadReservation.state)) == "failed"
    fields["sha256"] = hashlib.sha256(data).hexdigest()
    assert (
        client.post(
            path, headers=auth, data=fields, files={"audio": ("part.webm", data, "audio/webm")}
        ).status_code
        == 201
    )


def test_rate_bucket_is_atomic_across_threads(client):
    from sqlalchemy import event

    from app.database import SessionLocal, engine
    from app.limits import consume
    from app.models import RateBucket

    db_clock = ["2026-10-22 12:00:00"]

    def freeze_clock(connection, record):
        connection.create_function("current_timestamp", 0, lambda: db_clock[0])

    def attempt(_):
        try:
            consume("synthetic-limiter", "test", 5)
            return 200
        except HTTPException as error:
            return error.status_code

    # Admission is atomic per fixed window; real minute boundaries must not make this test flaky.
    event.listen(engine, "connect", freeze_clock)
    engine.dispose()
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(attempt, range(20)))
        assert statuses.count(200) == 5
        assert statuses.count(429) == 15
        db_clock[0] = "2026-10-22 12:01:00"
        assert attempt(None) == 200
        with SessionLocal() as db:
            assert sorted(db.scalars(select(RateBucket.count)).all()) == [1, 5]
    finally:
        event.remove(engine, "connect", freeze_clock)
        engine.dispose()


def test_body_limit_and_processing_readiness(client, auth, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MAX_JSON_BYTES", "1024")
    get_settings.cache_clear()
    assert client.post("/api/meetings", headers=auth, content=b"x" * 1025).status_code == 413
    ready = client.get("/api/ready").json()
    assert ready["status"] == "degraded" and ready["worker"] is False
    assert client.get("/api/processing-ready").status_code == 503


def test_wrapped_recovery_key_survives_rotation_without_plaintext_at_rest(
    client, auth, monkeypatch
):
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import LocalCacheKey

    first = base64.b64encode(b"1" * 32).decode()
    second = base64.b64encode(b"2" * 32).decode()
    monkeypatch.setenv("LOCAL_CACHE_KEK", first)
    get_settings.cache_clear()
    response = client.get("/api/local-cache/key", headers=auth)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    original = response.json()
    with SessionLocal() as db:
        row = db.scalar(select(LocalCacheKey))
        assert row.wrapped_key != original["keys"][0]["key"]
    monkeypatch.setenv("LOCAL_CACHE_KEK", second)
    monkeypatch.setenv("LOCAL_CACHE_KEK_ID", "v2")
    monkeypatch.setenv("LOCAL_CACHE_OLD_KEKS", '{"v1":"' + first + '"}')
    get_settings.cache_clear()
    assert client.get("/api/local-cache/key", headers=auth).json() == original
    monkeypatch.setenv("LOCAL_CACHE_OLD_KEKS", "{}")
    get_settings.cache_clear()
    assert client.get("/api/local-cache/key", headers=auth).json() == original


def test_job_effect_and_completion_rollback_together(client, auth):
    from app.database import SessionLocal
    from app.models import Job, Mom, now
    from app.mutations import finish_job

    mid = meeting(client, auth)
    with SessionLocal() as db:
        job = Job(
            meeting_id=mid,
            kind="generate_mom",
            status="running",
            claim_token="test",
            lease_until=now() + timedelta(minutes=1),
        )
        db.add(job)
        db.commit()
        jid = job.id
    with SessionLocal() as db:
        db.add(Mom(meeting_id=mid, status="draft", data={"title": "Synthetic"}))
        finish_job(db, jid, "test")
        db.rollback()
    with SessionLocal() as db:
        assert db.get(Mom, mid) is None
        assert db.get(Job, jid).status == "running"
        db.add(Mom(meeting_id=mid, status="draft", data={"title": "Synthetic"}))
        finish_job(db, jid, "test")
        db.commit()
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "completed"
        assert db.get(Job, jid).effect_committed_at is not None


def test_provider_permit_and_cooldown_apply_between_sessions(client, auth):
    from app.admission import acquire, current_job, release
    from app.database import SessionLocal
    from app.models import Job
    from app.providers import ProviderError

    mid = meeting(client, auth)
    with SessionLocal() as db:
        item = Job(meeting_id=mid, owner_actor="local-user", kind="generate_mom")
        db.add(item)
        db.commit()
        jid = item.id
    token = current_job.set(jid)
    try:
        permit = acquire("llm")
        with pytest.raises(ProviderError, match="busy"):
            acquire("llm")
        release("llm", permit, ProviderError("Outage", retryable=True, retry_after=30))
        with pytest.raises(ProviderError, match="cooling down"):
            acquire("llm")
    finally:
        current_job.reset(token)


def test_empty_staging_write_releases_storage_reservation(client, auth):
    from app.audio_store import abort_reservation, reserve, write_reserved
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import ActorUsage
    from app.mutations import lock_meeting

    mid = meeting(client, auth)
    with SessionLocal() as db:
        item = reserve(db, lock_meeting(db, mid), "test", 1024, get_settings())
        with pytest.raises(HTTPException):
            write_reserved(item, BytesIO(), 1024)
        abort_reservation(db, item)
        assert db.get(ActorUsage, "local-user").reserved_bytes == 0


def test_approval_cannot_pass_an_editor_holding_the_meeting_lock(client, auth):
    import threading

    from sqlalchemy import event

    from app.database import SessionLocal, engine
    from app.models import Mom
    from app.routes.minutes import approve, edit_mom
    from app.schemas import Approval, MomEdit

    mid = meeting(client, auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    with SessionLocal() as db:
        db.add(
            Mom(meeting_id=mid, status="draft", data={"title": "Synthetic", "summary": "Before"})
        )
        db.commit()
    paused = threading.Event()
    release = threading.Event()
    approving = threading.Event()

    def hold_editor(connection, cursor, statement, parameters, context, many):
        name = threading.current_thread().name
        if name.startswith("inflight-editor") and "FROM jobs" in statement:
            paused.set()
            if not release.wait(10):
                raise RuntimeError("Synthetic editor barrier timed out")
        if name.startswith("inflight-approval") and "BEGIN IMMEDIATE" in statement:
            approving.set()

    def edit():
        with SessionLocal() as db:
            return edit_mom(
                mid,
                MomEdit(title="Synthetic", summary="Edited", expected_revision=0),
                db,
                "local-user",
            )

    def approve_stale():
        with SessionLocal() as db:
            try:
                return approve(mid, Approval(approved=True, expected_revision=0), db, "local-user")
            except HTTPException as error:
                return error.status_code

    event.listen(engine, "before_cursor_execute", hold_editor)
    try:
        with (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="inflight-editor") as editors,
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="inflight-approval") as approvals,
        ):
            editing = editors.submit(edit)
            assert paused.wait(5)
            approval = approvals.submit(approve_stale)
            assert approving.wait(5)
            assert not approval.done()
            release.set()
            assert editing.result(timeout=10)["revision"] == 1
            assert approval.result(timeout=10) == 409
        with SessionLocal() as db:
            assert db.get(Mom, mid).status == "draft"
            assert db.get(Mom, mid).data["summary"] == "Edited"
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", hold_editor)
