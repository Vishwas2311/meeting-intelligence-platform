"""Deterministic crash/retention and failed-lease regressions on isolated data."""

import asyncio
import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select


def test_retention_resumes_after_unlink_failure_without_double_releasing_quota(
    client, auth, monkeypatch
):
    from app.audio_store import actor_usage
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import AudioChunk, CaptureManifest, Meeting, Mom, MomRevision, now
    from app.storage import expire_audio

    root = get_settings().storage_dir
    with SessionLocal() as db:
        m = Meeting(
            title="Synthetic archived",
            created_by="local-user",
            status="stopped",
            ended_at=now() - timedelta(days=60),
        )
        db.add(m)
        db.flush()
        mid = m.id
        folder = root / mid
        folder.mkdir()
        for i in range(2):
            path = folder / f"{i}.bin"
            path.write_bytes(b"x" * 10)
            db.add(
                AudioChunk(
                    meeting_id=mid,
                    sequence=i,
                    start_ms=i * 500,
                    end_ms=(i + 1) * 500,
                    media_type="audio/wav",
                    size_bytes=10,
                    sha256=hashlib.sha256(b"x" * 10).hexdigest(),
                    path=str(path),
                )
            )
        db.add(
            CaptureManifest(
                meeting_id=mid,
                data={
                    "expected_chunks": 2,
                    "recorded_through_ms": 1000,
                    "finalized": True,
                    "pauses": [],
                },
            )
        )
        db.add(
            Mom(
                meeting_id=mid,
                data={"title": "Synthetic", "summary": "Retained"},
                status="approved",
            )
        )
        db.add(
            MomRevision(
                meeting_id=mid, data={"title": "Synthetic"}, status="approved", actor="local-user"
            )
        )
        db.add_all(Meeting(title=f"Newer {i}") for i in range(10))
        db.commit()
        assert actor_usage(db, "local-user").used_bytes == 20
        db.commit()
    monkeypatch.setenv("AUDIO_RETENTION_ENABLED", "true")
    get_settings.cache_clear()
    original = Path.unlink

    def fail_second(path, *args, **kwargs):
        if path.name == "1.bin":
            raise OSError("Synthetic interruption")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_second)
        with pytest.raises(OSError):
            expire_audio()
    assert expire_audio() == 1
    assert expire_audio() == 0
    with SessionLocal() as db:
        assert {
            c.storage_state
            for c in db.scalars(select(AudioChunk).where(AudioChunk.meeting_id == mid))
        } == {"retired"}
        assert actor_usage(db, "local-user").used_bytes == 0
        assert db.get(Mom, mid).data["summary"] == "Retained"


def test_failed_heartbeat_cancels_work_and_prevents_commit(client, auth, monkeypatch):
    from app import worker
    from app.database import SessionLocal
    from app.models import Job, Meeting, Mom

    with SessionLocal() as db:
        m = Meeting(title="Synthetic lease", created_by="local-user")
        db.add(m)
        db.flush()
        db.add(Job(meeting_id=m.id, kind="generate_mom", owner_actor="local-user"))
        db.commit()
        mid = m.id

    async def renew(*args):
        await asyncio.sleep(0.01)
        raise RuntimeError("Synthetic heartbeat failure")

    async def work(kind, mid, jid, claim):
        await asyncio.sleep(0.2)
        with SessionLocal() as db:
            db.add(Mom(meeting_id=mid, data={"summary": "must never commit"}))
            db.commit()

    monkeypatch.setattr(worker, "heartbeat", renew)
    monkeypatch.setattr(worker, "execute", work)
    claimed = worker.acquire_job()
    asyncio.run(worker.run_job(*claimed))
    with SessionLocal() as db:
        assert db.get(Mom, mid) is None
        assert db.get(Job, claimed[0]).status == "failed"


def test_non_admin_can_check_recording_allowance_without_global_storage_access(
    client, auth, monkeypatch
):
    from app.config import get_settings

    monkeypatch.setenv("API_ADMIN_CLIENTS", "[]")
    get_settings.cache_clear()
    assert client.get("/api/storage", headers=auth).status_code == 403
    response = client.get("/api/storage/allowance", headers=auth)
    assert response.status_code == 200
    assert set(response.json()) == {"can_upload", "low_disk"}


def test_chunked_json_body_has_same_size_limit(client, auth, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MAX_JSON_BYTES", "1024")
    get_settings.cache_clear()
    response = client.post(
        "/api/meetings",
        headers={**auth, "Content-Type": "application/json"},
        content=iter([b'{"title":"', b"x" * 2048, b'"}']),
    )
    assert response.status_code == 413
