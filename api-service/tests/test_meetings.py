import asyncio
import hashlib

import pytest


def create(client, auth):
    r = client.post(
        "/api/meetings",
        headers=auth,
        json={"title": "Release planning", "source": "microphone", "consent_confirmed": True},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_auth_and_consent(client, auth):
    assert client.get("/api/meetings").status_code == 401
    r = client.post("/api/meetings", headers=auth, json={"title": "X", "consent_confirmed": False})
    assert r.status_code == 422


def test_combined_meeting_audio_source_is_saved(client, auth):
    response = client.post(
        "/api/meetings",
        headers=auth,
        json={"title": "Teams meeting", "source": "meeting-audio", "consent_confirmed": True},
    )
    assert response.status_code == 201
    mid = response.json()["id"]
    assert client.get(f"/api/meetings/{mid}", headers=auth).json()["source"] == "meeting-audio"


def test_history_timestamps_include_utc_timezone(client, auth):
    from datetime import UTC, datetime, timedelta, timezone

    from app.api_common import utc_time
    from app.database import SessionLocal
    from app.models import AuditEvent, Job, Meeting, Mom, MomRevision

    stamp = datetime(2026, 10, 21, 7, 36, 27, tzinfo=UTC)
    india = timezone(timedelta(hours=5, minutes=30))
    assert utc_time(None) is None
    assert utc_time(stamp.astimezone(india)) == stamp
    mid = create(client, auth)
    with SessionLocal() as db:
        meeting = db.get(Meeting, mid)
        meeting.created_at = meeting.started_at = meeting.ended_at = stamp
        db.add(Mom(meeting_id=mid, data={}, updated_at=stamp))
        db.add(
            MomRevision(
                meeting_id=mid, data={}, status="draft", actor="local-user", created_at=stamp
            )
        )
        db.add(Job(meeting_id=mid, kind="generate_mom", run_after=stamp, lease_until=stamp))
        db.add(AuditEvent(meeting_id=mid, actor="local-user", action="time.test", created_at=stamp))
        db.commit()

    meeting = client.get(f"/api/meetings/{mid}", headers=auth).json()
    history = client.get("/api/meetings", headers=auth).json()[0]
    minutes = client.get(f"/api/meetings/{mid}/mom", headers=auth).json()
    revision = client.get(f"/api/meetings/{mid}/mom/revisions", headers=auth).json()[0]
    job = client.get(f"/api/meetings/{mid}/jobs", headers=auth).json()[0]
    events = client.get(f"/api/meetings/{mid}/audit", headers=auth).json()
    event = next(e for e in events if e["action"] == "time.test")
    for value in (
        meeting["created_at"],
        meeting["started_at"],
        meeting["ended_at"],
        history["created_at"],
        minutes["updated_at"],
        revision["at"],
        job["run_after"],
        job["lease_until"],
        event["at"],
    ):
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None, value
        assert parsed == stamp
        assert parsed.astimezone(india).strftime("%H:%M:%S") == "13:06:27"


def test_lifecycle_transcript_idempotency(client, auth):
    mid = create(client, auth)
    assert client.post(f"/api/meetings/{mid}/start", headers=auth).status_code == 200
    r = client.post(
        f"/api/meetings/{mid}/capture-status",
        headers=auth,
        json={"capture_status": "active", "stt_status": "connected", "sync_status": "synced"},
    )
    assert r.json()["status"] == "recording"
    assert client.post(f"/api/meetings/{mid}/pause", headers=auth).json()["status"] == "paused"
    assert client.post(f"/api/meetings/{mid}/resume", headers=auth).json()["status"] == "starting"
    seg = {
        "client_event_id": "first",
        "text": "Action items are due Friday",
        "start_ms": 1000,
        "end_ms": 4500,
        "source": "live",
    }
    for expected in (1, 0):
        r = client.post(f"/api/meetings/{mid}/segments", headers=auth, json={"segments": [seg]})
        assert r.status_code == 200 and r.json()["accepted"] == expected
    assert len(client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()["segments"]) == 1
    assert client.post(f"/api/meetings/{mid}/stop", headers=auth).json()["status"] == "stopped"
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 202
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 202
    assert len(client.get(f"/api/meetings/{mid}/jobs", headers=auth).json()) == 1


def test_chunk_integrity(client, auth):
    mid = create(client, auth)
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    data = b"RIFFtestWAVE"
    digest = hashlib.sha256(data).hexdigest()
    kwargs = {
        "headers": auth,
        "data": {"sequence": "0", "start_ms": "0", "end_ms": "1000", "sha256": digest},
        "files": {"audio": ("part.wav", data, "audio/wav")},
    }
    assert client.post(f"/api/meetings/{mid}/audio-chunks", **kwargs).status_code == 201
    assert client.post(f"/api/meetings/{mid}/audio-chunks", **kwargs).json()["duplicate"] is True
    wrong = {**kwargs, "data": {**kwargs["data"], "sha256": "0" * 64}}
    assert client.post(f"/api/meetings/{mid}/audio-chunks", **wrong).status_code == 422


def test_unconfigured_azure_is_unavailable(client, auth):
    assert client.post("/api/speech/token", headers=auth).status_code == 503


def test_approved_minutes_cannot_be_overwritten(client, auth):
    from app.database import SessionLocal
    from app.models import Mom

    mid = create(client, auth)
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    data = {
        "title": "Reviewed minutes",
        "summary": "Keep the approved record",
        "transcript_gaps": [],
    }
    with SessionLocal() as db:
        db.add(Mom(meeting_id=mid, data=data, status="draft"))
        db.commit()
    assert (
        client.post(
            f"/api/meetings/{mid}/mom/approve", headers=auth, json={"approved": True}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/meetings/{mid}/mom", headers=auth, json={**data, "summary": "Overwrite"}
        ).status_code
        == 409
    )
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 409
    assert client.get(f"/api/meetings/{mid}/mom", headers=auth).json()["data"] == data
    assert client.get(f"/api/meetings/{mid}/jobs", headers=auth).json() == []


def test_duplicate_events_in_one_batch_are_idempotent(client, auth):
    mid = create(client, auth)
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    segment = {
        "client_event_id": "same-event",
        "text": "Keep the pilot small",
        "start_ms": 0,
        "end_ms": 1000,
    }
    for accepted, duplicates in [(1, 1), (0, 2)]:
        response = client.post(
            f"/api/meetings/{mid}/segments", headers=auth, json={"segments": [segment, segment]}
        )
        assert response.status_code == 200
        assert response.json() == {"accepted": accepted, "duplicates": duplicates}
    assert len(client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()["segments"]) == 1


def test_other_actor_cannot_read_or_change_meeting(client, auth):
    from app.database import SessionLocal
    from app.models import Meeting

    mid = create(client, auth)
    with SessionLocal() as db:
        db.get(Meeting, mid).created_by = "another-user"
        db.commit()
    assert client.get(f"/api/meetings/{mid}", headers=auth).status_code == 403
    assert client.post(f"/api/meetings/{mid}/start", headers=auth).status_code == 403
    assert client.get("/api/meetings", headers=auth).json() == []


def test_worker_does_not_replace_minutes_approved_during_generation(client, auth, monkeypatch):
    from app import worker
    from app.database import SessionLocal
    from app.models import Mom, Segment

    mid = create(client, auth)
    original = {"title": "Reviewed", "summary": "Keep the approved record"}
    with SessionLocal() as db:
        db.add(
            Segment(meeting_id=mid, client_event_id="one", text="Pilot", start_ms=0, end_ms=1000)
        )
        db.add(Mom(meeting_id=mid, data=original, status="draft"))
        db.commit()

    async def generate(*args):
        with SessionLocal() as db:
            db.get(Mom, mid).status = "approved"
            db.commit()
        return {"title": "Replacement", "summary": "Must not replace approval"}

    monkeypatch.setattr(worker, "llm_mom", generate)
    with pytest.raises(RuntimeError, match="Approved MoM"):
        asyncio.run(worker.execute("generate_mom", mid))
    assert client.get(f"/api/meetings/{mid}/mom", headers=auth).json()["data"] == original


def test_generate_edit_and_approve_minutes(client, auth, monkeypatch):
    from app import worker

    mid = create(client, auth)
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    segment = {
        "client_event_id": "pilot-decision",
        "text": "Limit the pilot to recording and transcript review.",
        "start_ms": 0,
        "end_ms": 2000,
    }
    assert (
        client.post(
            f"/api/meetings/{mid}/segments", headers=auth, json={"segments": [segment]}
        ).status_code
        == 200
    )
    assert client.post(f"/api/meetings/{mid}/stop", headers=auth).status_code == 200
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 202

    async def generate(settings, title, transcript, segment_ids, gaps):
        assert segment["text"] in transcript
        assert len(segment_ids) == 1
        return {"title": title, "summary": "A focused pilot was agreed.", "transcript_gaps": gaps}

    monkeypatch.setattr(worker, "llm_mom", generate)
    asyncio.run(worker.run_job(*worker.acquire_job()))
    assert client.get(f"/api/meetings/{mid}", headers=auth).json()["status"] == "ready_review"
    draft = client.get(f"/api/meetings/{mid}/mom", headers=auth).json()
    assert draft["status"] == "draft"
    draft["data"]["summary"] = "Reviewed: keep recording and transcript review in scope."
    edited = client.patch(f"/api/meetings/{mid}/mom", headers=auth, json=draft["data"])
    assert edited.status_code == 200
    assert (
        client.post(
            f"/api/meetings/{mid}/mom/approve", headers=auth, json={"approved": True}
        ).status_code
        == 200
    )
    approved = client.get(f"/api/meetings/{mid}/mom", headers=auth).json()
    assert approved["status"] == "approved"
    assert approved["data"]["summary"] == draft["data"]["summary"]
    assert (
        client.patch(f"/api/meetings/{mid}/mom", headers=auth, json=draft["data"]).status_code
        == 409
    )
    actions = [
        event["action"] for event in client.get(f"/api/meetings/{mid}/audit", headers=auth).json()
    ]
    assert actions[-3:] == ["mom.generated", "mom.edited", "mom.approved"]


@pytest.mark.parametrize(
    "method,path", [("POST", "form/preview"), ("POST", "form/submit"), ("GET", "submission")]
)
def test_removed_routes_return_not_found(client, auth, method, path):
    mid = create(client, auth)
    assert client.request(method, f"/api/meetings/{mid}/{path}", headers=auth).status_code == 404
