import asyncio
import hashlib
import io
import wave

import pytest


def ready_meeting(client, auth):
    mid = client.post(
        "/api/meetings",
        headers=auth,
        json={"title": "Speaker improvement", "consent_confirmed": True},
    ).json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0" * 128000)
    audio = buffer.getvalue()
    assert (
        client.post(
            f"/api/meetings/{mid}/audio-chunks",
            headers=auth,
            data={
                "sequence": 0,
                "start_ms": 0,
                "end_ms": 4000,
                "sha256": hashlib.sha256(audio).hexdigest(),
            },
            files={"audio": ("meeting.wav", audio, "audio/wav")},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/meetings/{mid}/manifest",
            headers=auth,
            json={
                "expected_chunks": 1,
                "recorded_through_ms": 4000,
                "finalized": True,
                "pauses": [],
            },
        ).status_code
        == 200
    )
    client.post(
        f"/api/meetings/{mid}/segments",
        headers=auth,
        json={
            "segments": [
                {
                    "client_event_id": "a",
                    "text": "Original first",
                    "start_ms": 0,
                    "end_ms": 2000,
                    "speaker_id": "live:a:Guest-1",
                },
                {
                    "client_event_id": "b",
                    "text": "Original second",
                    "start_ms": 2000,
                    "end_ms": 4000,
                    "speaker_id": "live:a:Guest-2",
                },
            ]
        },
    )
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    from app.database import SessionLocal
    from app.models import SpeakerName

    with SessionLocal() as db:
        db.add(SpeakerName(meeting_id=mid, speaker_id="live:a:Guest-1", display_name="Priya"))
        db.add(SpeakerName(meeting_id=mid, speaker_id="live:a:Guest-2", display_name="Karan"))
        db.commit()
    return mid


def improve(client, auth, monkeypatch, mid, phrases=None):
    from app import improvements, worker

    calls = []

    async def fast(*args, **kwargs):
        assert kwargs == {"diarization": True}
        calls.append(1)
        return {
            "phrases": phrases
            if phrases is not None
            else [
                {
                    "text": "Improved first",
                    "offsetMilliseconds": 0,
                    "durationMilliseconds": 2000,
                    "speaker": 1,
                },
                {
                    "text": "Improved second",
                    "offsetMilliseconds": 2000,
                    "durationMilliseconds": 2000,
                    "speaker": 0,
                },
            ]
        }

    monkeypatch.setattr(improvements, "fast_transcribe_file", fast)
    first = client.post(f"/api/meetings/{mid}/improve", headers=auth)
    assert first.status_code == 202, first.text
    repeated = client.post(f"/api/meetings/{mid}/improve", headers=auth)
    assert repeated.json()["job_id"] == first.json()["job_id"]
    asyncio.run(worker.run_job(*worker.acquire_job()))
    return client.get(f"/api/meetings/{mid}/improvement", headers=auth).json(), calls


def test_candidate_preserves_original_and_applies_only_confirmed_speaker_mapping(
    client, auth, monkeypatch
):
    mid = ready_meeting(client, auth)
    original = client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()
    comparison, calls = improve(client, auth, monkeypatch, mid)
    assert comparison["status"] == "completed" and calls == [1]
    duplicate = client.post(f"/api/meetings/{mid}/improve", headers=auth)
    assert duplicate.status_code == 202 and duplicate.json()["status"] == "completed"
    assert calls == [1]
    assert client.get(f"/api/meetings/{mid}/transcript", headers=auth).json() == original
    assert (
        len(comparison["candidate"]) == 2
        and comparison["speaker_names"]["live:a:Guest-1"] == "Priya"
    )
    first, second = [s["speaker_id"] for s in comparison["candidate"]]
    assert comparison["suggestions"] == {first: "live:a:Guest-1", second: "live:a:Guest-2"}
    path = f"/api/meetings/{mid}/improvement/apply"
    assert (
        client.post(
            path, headers=auth, json={"candidate_id": comparison["candidate_id"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path,
            headers=auth,
            json={
                "candidate_id": comparison["candidate_id"],
                "speaker_mapping": {first: "foreign", second: None},
            },
        ).status_code
        == 422
    )
    response = client.post(
        path,
        headers=auth,
        json={
            "candidate_id": comparison["candidate_id"],
            "speaker_mapping": {first: "live:a:Guest-1", second: None},
        },
    )
    assert response.status_code == 200, response.text
    result = client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()
    assert result["segments"][0]["speaker_id"] == "live:a:Guest-1"
    assert result["segments"][1]["speaker_id"] == second
    assert result["speaker_names"] == original["speaker_names"]
    from app.database import SessionLocal
    from app.models import Segment

    with SessionLocal() as db:
        assert all(db.get(Segment, s["id"]).text == s["text"] for s in original["segments"])


def test_keep_original_and_stale_candidate_cannot_overwrite_new_transcript(
    client, auth, monkeypatch
):
    mid = ready_meeting(client, auth)
    comparison, _ = improve(client, auth, monkeypatch, mid)
    before = client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()
    assert (
        client.post(
            f"/api/meetings/{mid}/improvement/apply",
            headers=auth,
            json={"candidate_id": comparison["candidate_id"], "keep_original": True},
        ).status_code
        == 200
    )
    assert client.get(f"/api/meetings/{mid}/transcript", headers=auth).json() == before
    from app.database import SessionLocal
    from app.models import Segment

    with SessionLocal() as db:
        db.add(
            Segment(
                meeting_id=mid,
                client_event_id="late",
                text="Late original",
                start_ms=100,
                end_ms=200,
            )
        )
        db.commit()
    assert (
        client.post(
            f"/api/meetings/{mid}/improvement/apply",
            headers=auth,
            json={"candidate_id": comparison["candidate_id"], "keep_original": True},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("speaker", [None, False, "0", -1])
def test_missing_or_invalid_speakers_never_inherit_names(client, auth, monkeypatch, speaker):
    mid = ready_meeting(client, auth)
    comparison, _ = improve(
        client,
        auth,
        monkeypatch,
        mid,
        [
            {
                "text": "Words",
                "offsetMilliseconds": 0,
                "durationMilliseconds": 1000,
                "speaker": speaker,
            }
        ],
    )
    assert comparison["status"] == ("completed" if speaker is None else "failed")
    if speaker is None:
        assert comparison["candidate"][0]["speaker_id"] is None
        assert comparison["suggestions"] == {}


def test_improvement_requires_owner_and_preserves_approved_minutes(client, auth, monkeypatch):
    mid = ready_meeting(client, auth)
    assert client.get(f"/api/meetings/{mid}/improvement").status_code == 401
    from app.database import SessionLocal
    from app.models import Meeting, Mom

    with SessionLocal() as db:
        db.add(
            Mom(
                meeting_id=mid, status="approved", data={"title": "Approved", "summary": "Original"}
            )
        )
        db.commit()
    assert client.post(f"/api/meetings/{mid}/improve", headers=auth).status_code == 409
    with SessionLocal() as db:
        db.get(Meeting, mid).created_by = "someone-else"
        db.commit()
    assert client.get(f"/api/meetings/{mid}/improvement", headers=auth).status_code == 403


def test_overlapping_and_short_turns_produce_no_identity_suggestion():
    from types import SimpleNamespace as Turn

    from app.improvements import suggestions

    live = [
        Turn(start_ms=0, end_ms=2000, speaker_id="a"),
        Turn(start_ms=0, end_ms=2000, speaker_id="b"),
    ]
    assert suggestions(live, [Turn(start_ms=0, end_ms=2000, speaker_id="audio")]) == {}
    assert suggestions(live[:1], [Turn(start_ms=0, end_ms=300, speaker_id="audio")]) == {}
