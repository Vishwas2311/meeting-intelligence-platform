import asyncio
import io
import subprocess
import wave
from uuid import uuid4

import pytest

from app.schemas import MomData


def create(client, auth, **extra):
    response = client.post(
        "/api/recaps",
        headers=auth,
        json={
            "id": str(uuid4()),
            "title": "Deployment recap",
            "meeting_date": "2026-10-20",
            "participants": [" Karan ", "Priya", "Karan"],
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * 32000)
    return buffer.getvalue()


def save(
    client,
    auth,
    recap,
    reviewed=True,
    notes="Karan will review the database. Friday needs confirmation.",
):
    return client.patch(
        f"/api/recaps/{recap['id']}/notes",
        headers=auth,
        json={
            "notes": notes,
            "reviewed": reviewed,
            "revision": recap["revision"],
        },
    )


def test_recap_is_distinct_from_meeting_capture_and_creation_is_idempotent(client, auth):
    recap = create(client, auth)
    mid = recap["id"]
    meeting = client.get(f"/api/meetings/{mid}", headers=auth).json()
    assert meeting["source"] == "personal-recap"
    assert meeting["consent_confirmed"] is False
    assert meeting["started_at"] is None
    assert recap["participants"] == ["Karan", "Priya"]
    assert recap["meeting_date"] == "2026-10-20"
    assert recap["source_label"] == "Based on personal recollection"
    repeated = create(client, auth, id=mid)
    assert repeated == recap
    assert (
        client.post("/api/meetings", headers=auth, json={"title": "Original meeting"}).status_code
        == 422
    )


def test_notes_autosave_metadata_and_clear_text_without_losing_prior_evidence(client, auth):
    recap = create(client, auth)
    path = f"/api/recaps/{recap['id']}/notes"
    response = client.patch(
        path,
        headers=auth,
        json={
            "notes": "",
            "reviewed": False,
            "revision": 0,
            "title": "Meeting notes · planning",
            "meeting_date": None,
            "participants": ["Priya", " Priya "],
            "language": "hi-IN",
        },
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved["title"] == "Meeting notes · planning"
    assert saved["participants"] == ["Priya"] and saved["meeting_date"] is None
    assert saved["language"] == "hi-IN"
    saved = save(client, auth, saved).json()
    cleared = save(client, auth, saved, reviewed=False, notes="").json()
    assert cleared["notes"] == "" and not cleared["reviewed"]
    assert client.post(f"/api/meetings/{recap['id']}/mom/generate", headers=auth).status_code == 409
    assert save(client, auth, cleared, reviewed=True, notes="").status_code == 422


def test_live_notes_audio_is_saved_without_queuing_paid_transcription(client, auth):
    recap = save(client, auth, create(client, auth), reviewed=False).json()
    mid = recap["id"]
    for _ in range(2):
        response = client.post(
            f"/api/recaps/{mid}/audio",
            headers=auth,
            data={"transcribe": "false"},
            files={"audio": ("notes.wav", wav(), "audio/wav")},
        )
        assert response.status_code == 202 and response.json() == {"status": "saved"}
    assert client.get(f"/api/meetings/{mid}/jobs", headers=auth).json() == []
    assert client.get(f"/api/recaps/{mid}", headers=auth).json()["notes"] == recap["notes"]
    assert client.get(f"/api/recaps/{mid}/audio", headers=auth).content == wav()


@pytest.mark.parametrize("choice", ["current", "audio"])
def test_audio_finalization_preserves_live_edits_until_explicit_choice(
    client, auth, monkeypatch, choice
):
    from app import recaps, worker

    recap = save(
        client,
        auth,
        create(client, auth),
        reviewed=False,
        notes="My manually corrected live notes.",
    ).json()
    mid = recap["id"]
    client.post(
        f"/api/recaps/{mid}/audio",
        headers=auth,
        data={"transcribe": "false"},
        files={"audio": ("notes.wav", wav(), "audio/wav")},
    )
    calls = []

    async def fast(*args):
        calls.append(1)
        return {
            "phrases": [
                {
                    "text": "Audio based final transcript.",
                    "offsetMilliseconds": 0,
                    "durationMilliseconds": 700,
                }
            ]
        }

    monkeypatch.setattr(recaps, "fast_transcribe_file", fast)
    first = client.post(f"/api/recaps/{mid}/finalize", headers=auth).json()
    assert (
        client.post(f"/api/recaps/{mid}/finalize", headers=auth).json()["job_id"] == first["job_id"]
    )
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 409
    asyncio.run(worker.run_job(*worker.acquire_job()))
    saved = client.get(f"/api/recaps/{mid}", headers=auth).json()
    assert saved["notes"] == recap["notes"]
    assert saved["finalization"]["live_notes"] == recap["notes"]
    assert saved["finalization"]["final_notes"] == "Audio based final transcript."
    assert saved["finalization"]["status"] == "completed"
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 409
    assert (
        client.post(
            f"/api/recaps/{mid}/finalize/choose",
            headers=auth,
            json={"choice": choice, "revision": 999},
        ).status_code
        == 409
    )
    accepted = client.post(
        f"/api/recaps/{mid}/finalize/choose",
        headers=auth,
        json={"choice": choice, "revision": saved["revision"]},
    ).json()
    assert accepted["notes"] == (
        recap["notes"] if choice == "current" else "Audio based final transcript."
    )
    assert accepted["finalization"]["status"] == "accepted" and not accepted["reviewed"]
    assert calls == [1]


def test_finalization_failure_preserves_notes_and_can_retry(client, auth, monkeypatch):
    from app import recaps, worker

    recap = save(client, auth, create(client, auth), reviewed=False).json()
    mid = recap["id"]
    client.post(
        f"/api/recaps/{mid}/audio",
        headers=auth,
        data={"transcribe": "false"},
        files={"audio": ("notes.wav", wav(), "audio/wav")},
    )

    async def fail(*args):
        raise RuntimeError("Synthetic transcription unavailable")

    monkeypatch.setattr(recaps, "fast_transcribe_file", fail)
    old = client.post(f"/api/recaps/{mid}/finalize", headers=auth).json()
    asyncio.run(worker.run_job(*worker.acquire_job()))
    saved = client.get(f"/api/recaps/{mid}", headers=auth).json()
    assert saved["notes"] == recap["notes"] and saved["audio_available"]
    assert saved["finalization"]["status"] == "failed"
    new = client.post(f"/api/recaps/{mid}/finalize", headers=auth).json()
    assert new["job_id"] != old["job_id"]


def test_review_is_required_and_notes_are_versioned_without_fake_meeting_times(client, auth):
    recap = create(client, auth)
    mid = recap["id"]
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 409
    saved = save(client, auth, recap, reviewed=False).json()
    assert saved["revision"] == 1 and saved["reviewed"] is False
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 409
    assert save(client, auth, recap, notes="A stale edit must not win").status_code == 409
    reviewed = save(client, auth, saved).json()
    transcript = client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()["segments"]
    assert len(transcript) == 1
    assert transcript[0]["source"] == "recap"
    assert transcript[0]["start_ms"] == transcript[0]["end_ms"] == 0
    assert transcript[0]["text"] == reviewed["notes"]


def test_typed_recollection_flows_through_existing_minutes_worker_and_approval(
    client, auth, monkeypatch
):
    from sqlalchemy import select

    from app import worker
    from app.database import SessionLocal
    from app.models import MomRevision

    recap = save(client, auth, create(client, auth)).json()
    observed = []

    async def minutes(settings, title, transcript, ids, gaps):
        observed.append(transcript)
        return MomData(
            title=title,
            summary="Personal recollection of the deployment discussion.",
            action_items=[
                {
                    "text": "Review database",
                    "owner": "Karan",
                    "due_date": None,
                    "source_segment_ids": ids,
                }
            ],
            unanswered_questions=["Confirm whether Friday is the deadline."],
        ).model_dump()

    monkeypatch.setattr(worker, "llm_mom", minutes)
    mid = recap["id"]
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 202
    assert save(client, auth, recap).status_code == 409
    asyncio.run(worker.run_job(*worker.acquire_job()))
    assert '"source_kind": "personal_recollection"' in observed[0]
    assert '"meeting_date": "2026-10-20"' in observed[0]
    assert "Friday needs confirmation" in observed[0]
    memo = client.get(f"/api/meetings/{mid}/mom", headers=auth).json()
    assert memo["status"] == "draft"
    assert memo["data"]["action_items"][0]["due_date"] is None
    with SessionLocal() as db:
        revision = db.scalar(select(MomRevision).where(MomRevision.meeting_id == mid))
        assert revision.provenance["source_kind"] == "personal_recollection"
    assert (
        client.post(
            f"/api/meetings/{mid}/mom/approve", headers=auth, json={"approved": True}
        ).status_code
        == 200
    )
    assert save(client, auth, recap, notes="Cannot overwrite approved source").status_code == 409


def test_phone_audio_upload_transcribes_with_azure_and_retains_original(client, auth, monkeypatch):
    from app import recaps, worker

    recap = create(client, auth, language="mr-IN")
    mid = recap["id"]
    observed = []

    async def fast(settings, audio, filename, media_type, language):
        observed.append(language)
        assert audio.startswith(b"RIFF") and filename == "recap.wav"
        return {
            "phrases": [
                {
                    "text": "बैठकीत डेटाबेसबद्दल चर्चा झाली.",
                    "offsetMilliseconds": 0,
                    "durationMilliseconds": 700,
                }
            ]
        }

    monkeypatch.setattr(recaps, "fast_transcribe_file", fast)
    uploaded = client.post(
        f"/api/recaps/{mid}/audio",
        headers=auth,
        files={"audio": ("phone.wav", wav(), "application/octet-stream")},
    )
    assert uploaded.status_code == 202
    duplicate = client.post(
        f"/api/recaps/{mid}/audio", headers=auth, files={"audio": ("phone.wav", wav(), "audio/wav")}
    )
    assert duplicate.json()["job_id"] == uploaded.json()["job_id"]
    assert save(client, auth, recap).status_code == 409
    asyncio.run(worker.run_job(*worker.acquire_job()))
    saved = client.get(f"/api/recaps/{mid}", headers=auth).json()
    assert saved["notes"] == "बैठकीत डेटाबेसबद्दल चर्चा झाली."
    assert saved["reviewed"] is False and saved["audio_available"] is True
    assert observed == ["mr-IN"]
    assert client.get(f"/api/recaps/{mid}/audio", headers=auth).content == wav()
    assert (
        client.get(f"/api/recaps/{mid}/audio", headers=auth).headers["content-type"] == "audio/wav"
    )
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 409


def test_invalid_upload_cannot_destroy_notes_or_consume_transcription(client, auth, monkeypatch):
    from app import recaps, worker

    recap = create(client, auth)
    mid = recap["id"]
    assert (
        client.post(
            f"/api/recaps/{mid}/audio",
            headers=auth,
            files={"audio": ("note.txt", b"not audio", "text/plain")},
        ).status_code
        == 415
    )
    assert (
        client.post(
            f"/api/recaps/{mid}/audio",
            headers=auth,
            files={"audio": ("note.wav", b"", "audio/wav")},
        ).status_code
        == 422
    )

    async def should_not_call(*args):
        raise AssertionError("Invalid audio must not be sent to Azure")

    monkeypatch.setattr(recaps, "fast_transcribe_file", should_not_call)
    assert (
        client.post(
            f"/api/recaps/{mid}/audio",
            headers=auth,
            files={"audio": ("note.wav", b"broken", "audio/wav")},
        ).status_code
        == 202
    )
    asyncio.run(worker.run_job(*worker.acquire_job()))
    jobs = client.get(f"/api/meetings/{mid}/jobs", headers=auth).json()
    assert jobs[0]["status"] == "failed" and "decoded" in jobs[0]["error"]
    assert client.get(f"/api/recaps/{mid}/audio", headers=auth).content == b"broken"
    assert save(client, auth, recap).status_code == 200


def test_recap_audio_size_limit_and_original_are_preserved(client, auth, monkeypatch):
    monkeypatch.setenv("MAX_RECAP_AUDIO_MB", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    recap = create(client, auth)
    mid = recap["id"]
    assert (
        client.post(
            f"/api/recaps/{mid}/audio",
            headers=auth,
            files={"audio": ("large.mp3", b"x" * (1024 * 1024 + 1), "audio/mpeg")},
        ).status_code
        == 413
    )
    assert client.get(f"/api/recaps/{mid}", headers=auth).json()["audio_available"] is False


def test_recap_endpoints_require_authentication_and_meeting_ownership(client, auth):
    recap = create(client, auth)
    mid = recap["id"]
    assert client.get(f"/api/recaps/{mid}").status_code == 401
    from app.database import SessionLocal
    from app.models import Meeting

    with SessionLocal() as db:
        db.get(Meeting, mid).created_by = "someone-else"
        db.commit()
    assert client.get(f"/api/recaps/{mid}", headers=auth).status_code == 403
    assert client.get(f"/api/recaps/{mid}/audio", headers=auth).status_code == 403
    assert save(client, auth, recap).status_code == 403
    assert (
        client.post(
            f"/api/recaps/{mid}/audio",
            headers=auth,
            files={"audio": ("voice.wav", wav(), "audio/wav")},
        ).status_code
        == 403
    )


def test_real_decoder_accepts_mobile_m4a_audio(client, auth, tmp_path):
    from imageio_ffmpeg import get_ffmpeg_exe

    from app.recaps import decode_voice_note

    source, target = tmp_path / "test.wav", tmp_path / "phone.m4a"
    source.write_bytes(wav())
    subprocess.run(
        [get_ffmpeg_exe(), "-v", "error", "-i", str(source), "-c:a", "aac", str(target)],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    data, duration = decode_voice_note(str(target))
    assert data.startswith(b"RIFF") and 900 <= duration <= 1200
