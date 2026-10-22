"""Accelerated duration/volume checks; these do not simulate cloud recognition quality."""

import asyncio
import hashlib
import subprocess
import time
from types import SimpleNamespace

import pytest


def test_two_hour_audio_upload_api(client, auth, monkeypatch):
    # This compresses two hours of writes into seconds; rate limits have their own concurrency test.
    from app.config import get_settings

    monkeypatch.setenv("ACTOR_REQUESTS_PER_MINUTE", "10000")
    monkeypatch.setenv("ACTOR_UPLOADS_PER_MINUTE", "10000")
    get_settings.cache_clear()
    mid = client.post(
        "/api/meetings",
        headers=auth,
        json={
            "title": "Synthetic API volume",
            "consent_confirmed": True,
        },
    ).json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    payload = b"synthetic audio payload" * 900
    digest = hashlib.sha256(payload).hexdigest()
    started = time.perf_counter()
    for i in range(1440):
        response = client.post(
            f"/api/meetings/{mid}/audio-chunks",
            headers=auth,
            data={
                "sequence": str(i),
                "start_ms": str(i * 5000),
                "end_ms": str((i + 1) * 5000),
                "sha256": digest,
            },
            files={"audio": ("part.webm", payload, "audio/webm")},
        )
        assert response.status_code == 201, response.text
    assert (
        client.post(
            f"/api/meetings/{mid}/manifest",
            headers=auth,
            json={
                "expected_chunks": 1440,
                "recorded_through_ms": 7200000,
                "finalized": True,
                "pauses": [],
            },
        ).status_code
        == 200
    )
    coverage = client.get(f"/api/meetings/{mid}/coverage", headers=auth).json()
    assert coverage["audio_capture_claimed_complete"]
    assert coverage["persisted_audio_chunks"] == 1440
    print(
        f"API chunks=1440 bytes={len(payload) * 1440} seconds={time.perf_counter() - started:.3f}"
    )


@pytest.mark.parametrize("hours,verbose", [(1, False), (2, False), (3, False), (2, True)])
def test_long_meeting_volume(client, auth, monkeypatch, hours, verbose):
    from app import worker
    from app.database import SessionLocal
    from app.models import AudioChunk, CaptureManifest, Segment
    from app.recovery import audio_coverage

    mid = client.post(
        "/api/meetings",
        headers=auth,
        json={"title": "Synthetic long meeting", "consent_confirmed": True},
    ).json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    duration = hours * 3_600_000
    count = duration // 5000
    with SessionLocal() as db:
        db.add(
            CaptureManifest(
                meeting_id=mid,
                data={
                    "expected_chunks": count,
                    "recorded_through_ms": duration,
                    "finalized": True,
                    "pauses": [],
                },
            )
        )
        for i in range(count):
            db.add(
                AudioChunk(
                    meeting_id=mid,
                    sequence=i,
                    start_ms=i * 5000,
                    end_ms=(i + 1) * 5000,
                    sha256="0" * 64,
                    media_type="audio/webm",
                    path="synthetic-unused",
                    size_bytes=10000,
                )
            )
            db.add(
                Segment(
                    meeting_id=mid,
                    client_event_id=f"synthetic-{i}",
                    text="We discussed project delivery, ownership and next steps. " * 3,
                    start_ms=i * 5000,
                    end_ms=(i + 1) * 5000,
                    speaker_id=f"speaker-{i % 3}",
                )
            )
        db.commit()
        assert audio_coverage(db, mid)["audio_capture_claimed_complete"]
    calls = []

    async def fake(settings, title, transcript, ids, gaps):
        assert len(transcript) <= settings.llm_max_input_chars
        calls.append((len(transcript), len(ids)))
        return {
            "title": title,
            "summary": "Synthetic summary. " * (100 if verbose else 1),
            "transcript_gaps": [],
        }

    monkeypatch.setattr(worker, "llm_mom", fake)
    started = time.perf_counter()
    if verbose:
        with pytest.raises(RuntimeError, match="Too many long-meeting summaries"):
            asyncio.run(worker.execute("generate_mom", mid))
        assert client.get(f"/api/meetings/{mid}/mom", headers=auth).status_code == 404
        print(f"MERGE hours={hours} calls={len(calls)} oversized merge safely rejected")
        return
    asyncio.run(worker.execute("generate_mom", mid))
    assert client.get(f"/api/meetings/{mid}/mom", headers=auth).status_code == 200
    assert calls[-1][1] == count
    print(
        f"VOLUME hours={hours} chunks={count} segments={count} calls={len(calls)} seconds={time.perf_counter() - started:.3f}"
    )


@pytest.mark.parametrize("seconds", [3600, 7200, 7201, 10800])
def test_real_decoder_duration_boundary(tmp_path, seconds):
    from imageio_ffmpeg import get_ffmpeg_exe

    from app.recovery import decode_audio

    source = tmp_path / "synthetic.flac"
    subprocess.run(
        [
            get_ffmpeg_exe(),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            str(seconds),
            "-c:a",
            "flac",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    data = source.read_bytes()
    chunk = SimpleNamespace(
        path=str(source), size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
    )
    manifest = {"recorded_through_ms": seconds * 1000, "pauses": []}
    started = time.perf_counter()
    if seconds > 7200:
        with pytest.raises(RuntimeError, match="duration"):
            decode_audio([chunk], manifest)
        result = "rejected"
    else:
        wav, duration = decode_audio([chunk], manifest)
        assert duration == seconds * 1000
        assert len(wav) >= seconds * 32000
        result = f"accepted wav_bytes={len(wav)}"
    print(f"DECODE seconds={seconds} {result} elapsed={time.perf_counter() - started:.3f}")
