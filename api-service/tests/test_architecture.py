import asyncio
import hashlib
import io
import json
import sqlite3
import wave
import zipfile

import pytest
from sqlalchemy import select, text

from app.audio_store import storage_path
from app.providers import parse_minutes


def session(client, auth):
    mid = client.post(
        "/api/meetings",
        headers=auth,
        json={"title": "Synthetic architecture test", "consent_confirmed": True},
    ).json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    client.post(
        f"/api/meetings/{mid}/segments",
        headers=auth,
        json={
            "segments": [
                {
                    "client_event_id": "live",
                    "text": "Review the pilot.",
                    "start_ms": 0,
                    "end_ms": 500,
                }
            ]
        },
    )
    return mid


def wav_recording(client, auth, mid):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * 32000)
    content = buffer.getvalue()
    response = client.post(
        f"/api/meetings/{mid}/audio-chunks",
        headers=auth,
        files={"audio": ("test.wav", content, "audio/wav")},
        data={
            "sequence": 0,
            "start_ms": 0,
            "end_ms": 1000,
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    assert response.status_code == 201
    return content


@pytest.mark.parametrize(
    "data",
    [
        {"title": "", "summary": "Summary"},
        {"title": "Title", "summary": " "},
        {
            "title": "Title",
            "summary": "Summary",
            "decisions": [{"text": "Invented", "source_segment_id": "unknown"}],
        },
        {
            "title": "Title",
            "summary": "Summary",
            "action_items": [{"text": "Task", "source_segment_ids": ["unknown"]}],
        },
        {"title": "Title", "summary": "Summary", "decisions": [{"text": "No evidence"}]},
    ],
)
def test_minutes_reject_blank_or_unsupported_evidence(data):
    with pytest.raises(ValueError):
        parse_minutes(json.dumps(data), [], ["real"])


def test_durable_loss_can_arrive_after_stop_and_is_idempotent(client, auth):
    mid = session(client, auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    event = {"id": "saved-in-browser", "at_ms": 500, "reason": "network-outage"}
    for _ in range(2):
        assert client.post(f"/api/meetings/{mid}/loss-events", headers=auth, json=event).json()[
            "saved"
        ]
    events = client.get(f"/api/meetings/{mid}/audit", headers=auth).json()
    assert sum(e["action"] == "transcript.loss" for e in events) == 1
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 409


@pytest.mark.parametrize("expected,end", [(2, 1000), (1, 2000)])
def test_manifest_detects_missing_chunks_and_missing_tail(client, auth, expected, end):
    mid = session(client, auth)
    wav_recording(client, auth, mid)
    assert (
        client.post(
            f"/api/meetings/{mid}/manifest",
            headers=auth,
            json={"expected_chunks": expected, "recorded_through_ms": end, "finalized": True},
        ).status_code
        == 200
    )
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    assert (
        client.get(f"/api/meetings/{mid}/coverage", headers=auth).json()[
            "audio_capture_claimed_complete"
        ]
        is False
    )
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 409


def test_pause_manifest_validation_and_finalization(client, auth):
    mid = session(client, auth)
    body = {
        "expected_chunks": 1,
        "recorded_through_ms": 1000,
        "finalized": True,
        "pauses": [{"start_ms": 800, "end_ms": 1200}],
    }
    assert client.post(f"/api/meetings/{mid}/manifest", headers=auth, json=body).status_code == 422
    body["pauses"] = []
    assert client.post(f"/api/meetings/{mid}/manifest", headers=auth, json=body).status_code == 200
    assert (
        client.post(
            f"/api/meetings/{mid}/manifest", headers=auth, json={**body, "expected_chunks": 2}
        ).status_code
        == 409
    )


def test_recovery_decodes_audio_preserves_original_and_clears_only_verified_losses(
    client, auth, monkeypatch
):
    from app import recovery, worker
    from app.database import SessionLocal
    from app.models import Segment

    mid = session(client, auth)
    wav_recording(client, auth, mid)
    client.post(
        f"/api/meetings/{mid}/manifest",
        headers=auth,
        json={"expected_chunks": 1, "recorded_through_ms": 1000, "finalized": True},
    )
    client.post(
        f"/api/meetings/{mid}/loss-events",
        headers=auth,
        json={"id": "loss", "at_ms": 0, "reason": "sdk-disconnected"},
    )
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    monkeypatch.setenv("AZURE_SPEECH_FAST_ENDPOINT", "https://synthetic.invalid/transcribe")
    from app.config import get_settings

    get_settings.cache_clear()

    async def fast(settings, audio, filename, typ, language):
        assert audio.startswith(b"RIFF") and language == "auto"
        return {
            "phrases": [
                {
                    "text": "Review the entire pilot.",
                    "offsetMilliseconds": 0,
                    "durationMilliseconds": 800,
                }
            ]
        }

    monkeypatch.setattr(recovery, "fast_transcribe_file", fast)
    assert client.post(f"/api/meetings/{mid}/recover", headers=auth).status_code == 202
    asyncio.run(worker.run_job(*worker.acquire_job()))
    data = client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()["segments"]
    assert len(data) == 1 and data[0]["source"] == "recovered"
    assert (
        client.get(f"/api/meetings/{mid}/coverage", headers=auth).json()["transcript_incomplete"]
        is False
    )
    with SessionLocal() as db:
        assert len(db.scalars(select(Segment).where(Segment.meeting_id == mid)).all()) == 2
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 202
    client.post(
        f"/api/meetings/{mid}/loss-events",
        headers=auth,
        json={"id": "later-loss", "at_ms": 900, "reason": "late-report"},
    )
    assert (
        client.get(f"/api/meetings/{mid}/coverage", headers=auth).json()["transcript_incomplete"]
        is True
    )


def test_loss_reported_during_generation_prevents_commit(client, auth, monkeypatch):
    from app import worker

    mid = session(client, auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    client.post(f"/api/meetings/{mid}/mom/generate", headers=auth)

    async def llm(*args):
        client.post(
            f"/api/meetings/{mid}/loss-events",
            headers=auth,
            json={"id": "late", "at_ms": 0, "reason": "late-sync"},
        )
        return {"title": "Synthetic", "summary": "Partial summary"}

    monkeypatch.setattr(worker, "llm_mom", llm)
    asyncio.run(worker.run_job(*worker.acquire_job()))
    assert client.get(f"/api/meetings/{mid}/mom", headers=auth).status_code == 404


def test_backup_restore_checksums_and_foreign_keys(client, auth, tmp_path):
    from app.database import SessionLocal
    from app.storage import backup, restore

    mid = session(client, auth)
    wav_recording(client, auth, mid)
    archive, target = tmp_path / "complete.zip", tmp_path / "restored"
    backup(archive)
    restore(archive, target)
    with sqlite3.connect(target / "meeting_intelligence.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 1
        path, expected = db.execute("SELECT path,sha256 FROM audio_chunks").fetchone()

        assert hashlib.sha256(storage_path(path).read_bytes()).hexdigest() == expected
    with pytest.raises(RuntimeError, match="empty"):
        restore(archive, target)
    with (
        zipfile.ZipFile(archive) as source,
        zipfile.ZipFile(tmp_path / "corrupt.zip", "w") as corrupt,
    ):
        for name in source.namelist():
            corrupt.writestr(name, b"broken" if name.startswith("audio/") else source.read(name))
    with pytest.raises(RuntimeError, match="checksum"):
        restore(tmp_path / "corrupt.zip", tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
    with SessionLocal() as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_approved_revision_is_immutable_and_explicitly_forked(client, auth):
    from app.database import SessionLocal
    from app.models import Mom

    mid = session(client, auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    with SessionLocal() as db:
        db.add(Mom(meeting_id=mid, data={"title": "Original", "summary": "Agreed summary"}))
        db.commit()
    assert (
        client.post(
            f"/api/meetings/{mid}/mom/approve", headers=auth, json={"approved": True}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/meetings/{mid}/mom", headers=auth, json={"title": "Changed", "summary": "New"}
        ).status_code
        == 409
    )
    assert client.post(f"/api/meetings/{mid}/mom/revise", headers=auth).status_code == 200
    assert (
        client.patch(
            f"/api/meetings/{mid}/mom", headers=auth, json={"title": "Changed", "summary": "New"}
        ).status_code
        == 200
    )
    versions = client.get(f"/api/meetings/{mid}/mom/revisions", headers=auth).json()
    assert any(v["status"] == "approved" and v["data"]["title"] == "Original" for v in versions)


def test_history_pagination_and_worker_readiness(client, auth):
    from app.database import SessionLocal
    from app.models import Meeting, WorkerState, now

    with SessionLocal() as db:
        db.add_all(Meeting(title=f"History {i}", consent_confirmed=True) for i in range(105))
        db.commit()
    a = client.get("/api/meetings?limit=100", headers=auth).json()
    b = client.get("/api/meetings?offset=100&limit=100", headers=auth).json()
    assert len(a) == 100 and len(b) == 5 and not set(m["id"] for m in a) & set(m["id"] for m in b)
    assert len(client.get("/api/meetings?q=History%20104", headers=auth).json()) == 1
    assert client.get("/api/ready").json()["worker"] is False
    with SessionLocal() as db:
        db.add(WorkerState(id="local", seen_at=now()))
        db.commit()
    assert client.get("/api/ready").json()["worker"] is True


def test_legacy_approved_actions_display_without_rewriting_original(client, auth):
    from app.database import SessionLocal
    from app.models import Mom, Segment

    mid = session(client, auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.meeting_id == mid))
        original = {
            "title": "Legacy minutes",
            "summary": "Original summary",
            "action_items": [{"action": "Review the pilot", "source_segment_id": segment.id}],
        }
        db.add(Mom(meeting_id=mid, data=original, status="approved"))
        db.commit()
    shown = client.get(f"/api/meetings/{mid}/mom", headers=auth).json()
    assert shown["data"]["action_items"][0]["text"] == "Review the pilot"
    assert shown["data"]["action_items"][0]["source_segment_ids"] == [segment.id]
    with SessionLocal() as db:
        assert db.get(Mom, mid).data == original
    assert client.post(f"/api/meetings/{mid}/mom/revise", headers=auth).status_code == 200
    draft = client.get(f"/api/meetings/{mid}/mom", headers=auth).json()["data"]
    assert client.patch(f"/api/meetings/{mid}/mom", headers=auth, json=draft).status_code == 200


def test_opt_in_retention_protects_history_and_leaves_text_usable(client, auth, monkeypatch):
    from datetime import timedelta

    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import AudioChunk, Meeting, Mom, MomRevision, now
    from app.recovery import require_complete_audio
    from app.storage import expire_audio

    mid = session(client, auth)
    wav_recording(client, auth, mid)
    client.post(
        f"/api/meetings/{mid}/manifest",
        headers=auth,
        json={"expected_chunks": 1, "recorded_through_ms": 1000, "finalized": True},
    )
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    with SessionLocal() as db:
        mom = {"title": "Archived", "summary": "Verified original"}
        db.add(Mom(meeting_id=mid, data=mom, status="approved"))
        db.add(MomRevision(meeting_id=mid, data=mom, status="approved", actor="local-user"))
        db.get(Meeting, mid).ended_at = now() - timedelta(days=60)
        path = storage_path(db.scalar(select(AudioChunk.path).where(AudioChunk.meeting_id == mid)))
        db.commit()
    assert expire_audio() == 0 and path.exists()
    monkeypatch.setenv("AUDIO_RETENTION_ENABLED", "true")
    get_settings.cache_clear()
    assert expire_audio() == 0 and path.exists()  # The latest ten are always protected.
    with SessionLocal() as db:
        db.add_all(Meeting(title=f"Newer {i}", consent_confirmed=True) for i in range(10))
        db.commit()
    assert expire_audio() == 1 and not path.exists()
    with SessionLocal() as db:
        assert db.get(Mom, mid).data["summary"] == "Verified original"
        assert require_complete_audio(db, mid)["audio_capture_claimed_complete"] is False
    assert client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()["segments"]


def test_local_launcher_refuses_missing_node(monkeypatch):
    from scripts import run_local_ui

    monkeypatch.setenv("NODE_EXE", "missing-node-executable")
    with pytest.raises(SystemExit, match="Install Node.js"):
        run_local_ui.main()


def test_local_launcher_refuses_missing_package_environment(monkeypatch, tmp_path):
    import sys

    from scripts import run_local_ui

    monkeypatch.setenv("NODE_EXE", sys.executable)
    monkeypatch.setattr(run_local_ui, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="Create api-service/.env"):
        run_local_ui.main()
