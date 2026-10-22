import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest


def started(client, auth):
    response = client.post(
        "/api/meetings",
        headers=auth,
        json={"title": "Synthetic negative test", "consent_confirmed": True},
    )
    mid = response.json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    client.post(
        f"/api/meetings/{mid}/capture-status",
        headers=auth,
        json={"capture_status": "active", "stt_status": "connected", "sync_status": "synced"},
    )
    return mid


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 256},
        {"title": "Test", "language": ""},
        {"title": "Test", "language": "   "},
        {"title": "Test", "language": "x" * 21},
        {"title": "Test", "source": "unknown"},
    ],
)
def test_invalid_meeting_configuration_is_rejected(client, auth, body):
    response = client.post("/api/meetings", headers=auth, json={**body, "consent_confirmed": True})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"start_ms": -1},
        {"end_ms": -1},
        {"start_ms": 10, "end_ms": 0},
        {"text": ""},
        {"text": "   "},
        {"client_event_id": ""},
        {"speaker_id": "x" * 101},
    ],
)
def test_invalid_transcript_data_is_rejected(client, auth, change):
    mid = started(client, auth)
    segment = {"client_event_id": "test", "text": "Synthetic speech", "start_ms": 0, "end_ms": 100}
    response = client.post(
        f"/api/meetings/{mid}/segments", headers=auth, json={"segments": [{**segment, **change}]}
    )
    assert response.status_code == 422
    assert client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()["segments"] == []


def test_conflicting_transcript_retry_cannot_silently_replace_or_hide_text(client, auth):
    mid = started(client, auth)
    segment = {"client_event_id": "test", "text": "Original speech", "start_ms": 0, "end_ms": 100}
    url = f"/api/meetings/{mid}/segments"
    assert client.post(url, headers=auth, json={"segments": [segment]}).status_code == 200
    assert client.post(url, headers=auth, json={"segments": [segment]}).json()["duplicates"] == 1
    assert (
        client.post(
            url, headers=auth, json={"segments": [{**segment, "text": "Different speech"}]}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "data,media,timeline,expected",
    [
        (b"", "audio/webm", (0, 100), 422),
        (b"synthetic", "text/html", (0, 100), 415),
        (b"synthetic", "audio/webm", (-1, 100), 422),
        (b"synthetic", "audio/webm", (100, 0), 422),
        (b"synthetic", "audio/webm", (0.25, 100), 422),
    ],
)
def test_invalid_audio_is_not_persisted(client, auth, data, media, timeline, expected):
    mid = started(client, auth)
    response = client.post(
        f"/api/meetings/{mid}/audio-chunks",
        headers=auth,
        data={
            "sequence": 0,
            "start_ms": timeline[0],
            "end_ms": timeline[1],
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        files={"audio": ("test.webm", data, media)},
    )
    assert response.status_code == expected
    assert client.get(f"/api/meetings/{mid}/audio-chunks", headers=auth).json() == []


def test_upload_size_limit_does_not_write_partial_audio(client, auth, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MAX_AUDIO_CHUNK_MB", "1")
    get_settings.cache_clear()
    mid = started(client, auth)
    data = b"x" * (1024 * 1024 + 1)
    response = client.post(
        f"/api/meetings/{mid}/audio-chunks",
        headers=auth,
        data={
            "sequence": 0,
            "start_ms": 0,
            "end_ms": 1,
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        files={"audio": ("test.webm", data, "audio/webm")},
    )
    assert response.status_code == 413
    assert not (get_settings().storage_dir / mid).exists()


def test_capture_status_cannot_revive_an_unstarted_or_completed_meeting(client, auth):
    from app.database import SessionLocal
    from app.models import Meeting

    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Test", "consent_confirmed": True}
    ).json()["id"]
    body = {"capture_status": "active", "stt_status": "connected"}
    assert (
        client.post(f"/api/meetings/{mid}/capture-status", headers=auth, json=body).status_code
        == 409
    )
    with SessionLocal() as db:
        db.get(Meeting, mid).status = "ready_review"
        db.commit()
    assert (
        client.post(f"/api/meetings/{mid}/capture-status", headers=auth, json=body).status_code
        == 409
    )


def test_approval_and_edit_cannot_erase_recorded_transcript_loss(client, auth):
    from app.database import SessionLocal
    from app.models import Mom

    mid = started(client, auth)
    client.post(
        f"/api/meetings/{mid}/capture-status",
        headers=auth,
        json={
            "capture_status": "active",
            "stt_status": "disconnected",
            "transcript_incomplete": True,
        },
    )
    # A reconnect must not erase the earlier interruption.
    client.post(
        f"/api/meetings/{mid}/capture-status",
        headers=auth,
        json={"capture_status": "active", "stt_status": "connected"},
    )
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    with SessionLocal() as db:
        db.add(
            Mom(meeting_id=mid, data={"title": "Test", "summary": "Partial", "transcript_gaps": []})
        )
        db.commit()
    assert (
        client.post(
            f"/api/meetings/{mid}/mom/approve", headers=auth, json={"approved": True}
        ).status_code
        == 409
    )


def test_saved_minutes_cannot_cite_nonexistent_transcript_evidence(client, auth):
    from app.database import SessionLocal
    from app.models import Mom

    mid = started(client, auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    with SessionLocal() as db:
        db.add(Mom(meeting_id=mid, data={"title": "Test", "summary": "Original"}))
        db.commit()
    response = client.patch(
        f"/api/meetings/{mid}/mom",
        headers=auth,
        json={
            "title": "Test",
            "summary": "Edited",
            "decisions": [{"source_segment_ids": ["invented"]}],
        },
    )
    assert response.status_code == 422
    assert (
        client.get(f"/api/meetings/{mid}/mom", headers=auth).json()["data"]["summary"] == "Original"
    )


@pytest.mark.parametrize(
    "path", ["", "/transcript", "/audio-chunks", "/jobs", "/mom", "/audit", "/coverage"]
)
def test_other_owners_meetings_are_inaccessible(client, auth, path):
    from app.database import SessionLocal
    from app.models import Meeting

    with SessionLocal() as db:
        meeting = Meeting(title="Other owner", created_by="other-user", consent_confirmed=True)
        db.add(meeting)
        db.commit()
        mid = meeting.id
    assert client.get(f"/api/meetings/{mid}{path}", headers=auth).status_code == 403
    assert client.get(f"/api/meetings/{mid}{path}").status_code == 401


def test_simultaneous_generation_requests_queue_only_one_job(client, auth):
    mid = started(client, auth)
    client.post(
        f"/api/meetings/{mid}/segments",
        headers=auth,
        json={
            "segments": [
                {
                    "client_event_id": "one",
                    "text": "Synthetic discussion",
                    "start_ms": 0,
                    "end_ms": 100,
                }
            ]
        },
    )
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    barrier = Barrier(5)

    def queue():
        barrier.wait()
        return client.post(f"/api/meetings/{mid}/mom/generate", headers=auth)

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda _: queue(), range(5)))
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["job_id"] for response in responses}) == 1
    assert len(client.get(f"/api/meetings/{mid}/jobs", headers=auth).json()) == 1


def test_worker_keeps_polling_after_a_temporary_database_failure(client, monkeypatch):
    import asyncio

    from sqlalchemy.exc import OperationalError

    from app import worker

    calls = []

    def acquire():
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("locked", {}, Exception("Synthetic database lock"))
        raise asyncio.CancelledError()

    async def sleep(seconds):
        return None

    monkeypatch.setattr(worker, "acquire_job", acquire)
    monkeypatch.setattr(worker.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.work_loop())
    assert len(calls) == 2


def test_long_individual_segments_that_fit_the_provider_limit_are_not_rejected(
    client, auth, monkeypatch
):
    import asyncio

    from app import worker
    from app.config import get_settings

    monkeypatch.setenv("LLM_MAX_INPUT_CHARS", "1000")
    get_settings.cache_clear()
    mid = started(client, auth)
    for i in range(2):
        client.post(
            f"/api/meetings/{mid}/segments",
            headers=auth,
            json={
                "segments": [
                    {
                        "client_event_id": str(i),
                        "text": "Synthetic discussion. " * 32,
                        "start_ms": i * 1000,
                        "end_ms": (i + 1) * 1000,
                    }
                ]
            },
        )
    client.post(f"/api/meetings/{mid}/stop", headers=auth)

    async def minutes(settings, title, transcript, ids, gaps):
        assert len(transcript) <= settings.llm_max_input_chars
        return {"title": title, "summary": "Synthetic section", "transcript_gaps": gaps}

    monkeypatch.setattr(worker, "llm_mom", minutes)
    asyncio.run(worker.execute("generate_mom", mid))
    assert client.get(f"/api/meetings/{mid}/mom", headers=auth).status_code == 200


def test_completed_meetings_accept_upload_ack_retries_but_not_new_audio(client, auth):
    from app.database import SessionLocal
    from app.models import Meeting

    mid = started(client, auth)
    data = b"Synthetic audio"
    kwargs = {
        "headers": auth,
        "data": {
            "sequence": 0,
            "start_ms": 0,
            "end_ms": 100,
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "files": {"audio": ("chunk.webm", data, "audio/webm")},
    }
    assert client.post(f"/api/meetings/{mid}/audio-chunks", **kwargs).status_code == 201
    with SessionLocal() as db:
        db.get(Meeting, mid).status = "ready_review"
        db.commit()
    assert client.post(f"/api/meetings/{mid}/audio-chunks", **kwargs).json()["duplicate"] is True
    kwargs["data"]["sequence"] = 1
    assert client.post(f"/api/meetings/{mid}/audio-chunks", **kwargs).status_code == 409
