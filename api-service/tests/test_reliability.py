import asyncio
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import inspect, text

from app.audio_store import storage_path
from app.config import BACKEND_DIR, Settings
from app.providers import ProviderError, llm_mom, parse_minutes, retry_delay


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_speech_failure_explains_authentication_without_leaking_provider_data(monkeypatch, status):
    from fastapi import HTTPException

    from app.providers import azure_speech_token

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, text="PRIVATE PROVIDER RESPONSE")
            ),
            **kw,
        ),
    )
    settings = Settings(
        _env_file=None, azure_speech_region="centralindia", azure_speech_key="test-secret"
    )
    with pytest.raises(HTTPException) as failure:
        asyncio.run(azure_speech_token(settings))
    assert failure.value.status_code == (502 if status == 500 else 503)
    detail = failure.value.detail
    assert "PRIVATE" not in detail and "test-secret" not in detail and "https://" not in detail
    if status in (401, 403):
        assert str(status) in detail and "service configuration" in detail
    if status == 429:
        assert "rate limit" in detail


@pytest.mark.parametrize("value,expected", [("2500", 2.5), ("60000", 60)])
def test_azure_millisecond_retry_header(value, expected):
    assert retry_delay(httpx.Headers({"retry-after-ms": value})) == expected


def test_retry_after_seconds_date_and_invalid_values():
    assert retry_delay(httpx.Headers({"Retry-After": "1000"})) == 1000
    assert 60 <= retry_delay(httpx.Headers({"Retry-After": "invalid"})) <= 65
    assert 60 <= retry_delay(httpx.Headers({"Retry-After": "NaN"})) <= 65
    assert retry_delay(httpx.Headers({"Retry-After": "Thu, 01 Jan 2099 00:00:00 GMT"})) > 1000


def test_storage_paths_do_not_change_with_launch_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        _env_file=None, database_url="sqlite:///./history.db", storage_dir="./recordings"
    )
    from sqlalchemy.engine import make_url

    assert Path(make_url(settings.database_url).database) == BACKEND_DIR / "history.db"
    assert settings.storage_dir == BACKEND_DIR / "recordings"


@pytest.mark.parametrize(
    "content",
    [
        '{"title":"Pilot","summary":"Reviewed"}',
        '```json\n{"title":"Pilot","summary":"Reviewed"}\n```',
        'Here are the minutes: {"title":"Pilot","summary":"Reviewed"} End.',
        [{"text": '{"title":"Pilot",'}, {"text": '"summary":"Reviewed"}'}],
    ],
)
def test_provider_json_variants_keep_review_requirement(content):
    result = parse_minutes(content, [], [])
    assert result["summary"] == "Reviewed" and result["requires_review"] is True


def test_provider_rejects_invented_evidence():
    with pytest.raises(ValueError):
        parse_minutes(
            '{"title":"T","summary":"S","decisions":[{"source_segment_ids":["invented"]}]}',
            [],
            ["real"],
        )


@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (401, False), (400, False)])
def test_provider_errors_are_safe_and_retryable_by_status(monkeypatch, status, retryable):
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"retry-after-ms": "120000"}, text="PRIVATE PROVIDER DATA"
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    settings = Settings(
        _env_file=None,
        llm_endpoint="https://synthetic.openai.azure.com/openai/v1/chat/completions",
        llm_api_key="synthetic-secret",
        llm_model="synthetic",
    )
    with pytest.raises(ProviderError) as failure:
        asyncio.run(llm_mom(settings, "Synthetic", "Test", [], []))
    assert failure.value.retryable == retryable
    assert "PRIVATE" not in str(failure.value) and "https://" not in str(failure.value)
    assert calls[0].headers["api-key"] == "synthetic-secret"
    if status == 429:
        assert failure.value.retry_after == 120


@pytest.mark.parametrize(
    "finish,content,error_retry",
    [
        ("stop", '{"title":"Synthetic","summary":"Valid minutes"}', None),
        ("stop", "Invalid JSON", True),
        ("length", "{", False),
        ("content_filter", None, False),
    ],
)
def test_provider_success_and_invalid_output_contract(monkeypatch, finish, content, error_retry):
    import json

    original = httpx.AsyncClient

    def handler(request):
        assert json.loads(request.content)["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200, json={"choices": [{"finish_reason": finish, "message": {"content": content}}]}
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    settings = Settings(
        _env_file=None,
        llm_endpoint="https://synthetic.invalid/completions",
        llm_api_key="synthetic",
        llm_model="test",
    )
    if error_retry is None:
        assert (
            asyncio.run(llm_mom(settings, "T", "Synthetic", [], []))["summary"] == "Valid minutes"
        )
    else:
        with pytest.raises(ProviderError) as error:
            asyncio.run(llm_mom(settings, "T", "Synthetic", [], []))
        assert error.value.retryable == error_retry


@pytest.mark.parametrize(
    "content,diagnostic",
    [
        ("PRIVATE TRANSCRIPT not json", "invalid JSON or response structure"),
        (
            '{"title":"T","summary":"PRIVATE TRANSCRIPT","decisions":["wrong shape"]}',
            "schema validation (decisions)",
        ),
        (
            '{"title":"T","summary":"S","decisions":[{"text":"D","source_segment_ids":["invented"]}]}',
            "missing or unknown transcript evidence IDs",
        ),
    ],
)
def test_invalid_minutes_retry_prompt_and_diagnostics_are_safe(monkeypatch, content, diagnostic):
    import json

    original = httpx.AsyncClient

    def handler(request):
        prompt = json.loads(request.content)["messages"][0]["content"]
        assert "Required output schema" in prompt and "source_segment_ids" in prompt
        assert "Do not print segment IDs" in prompt
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    settings = Settings(
        _env_file=None,
        llm_endpoint="https://synthetic.invalid/completions",
        llm_api_key="secret",
        llm_model="test",
        llm_response_format="json_object",
    )
    with pytest.raises(ProviderError) as failure:
        asyncio.run(llm_mom(settings, "T", "S", ["real"], []))
    assert diagnostic in str(failure.value)
    assert "PRIVATE" not in str(failure.value) and "invented" not in str(failure.value)
    assert failure.value.retry_after == 1 and failure.value.max_attempts == 2


def test_invalid_minutes_retries_without_navigation_and_stops_after_two_attempts(
    client, auth, monkeypatch
):
    from app import worker
    from app.database import SessionLocal
    from app.models import Job, now

    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Retry check", "consent_confirmed": True}
    ).json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    client.post(
        f"/api/meetings/{mid}/segments",
        headers=auth,
        json={
            "segments": [
                {
                    "client_event_id": "turn",
                    "text": "Planning discussion",
                    "start_ms": 0,
                    "end_ms": 1000,
                }
            ]
        },
    )

    async def invalid(*args):
        raise ProviderError(
            "LLM minutes validation failed: invalid JSON or response structure.",
            retryable=True,
            retry_after=1,
            max_attempts=2,
        )

    monkeypatch.setattr(worker, "llm_mom", invalid)
    jid = client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).json()["job_id"]
    asyncio.run(worker.run_job(*worker.acquire_job()))
    pending = client.get(f"/api/meetings/{mid}/jobs", headers=auth).json()[0]
    assert pending["status"] == "pending" and pending["max_attempts"] == 2
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert (job.run_after.replace(tzinfo=now().tzinfo) - now()).total_seconds() <= 1
        job.run_after = now()
        db.commit()
    asyncio.run(worker.run_job(*worker.acquire_job()))
    failed = client.get(f"/api/meetings/{mid}/jobs", headers=auth).json()[0]
    assert failed["status"] == "failed" and failed["attempts"] == 2
    assert "Retrying" not in failed["error"]


def test_job_delay_survives_restart_and_only_failed_section_repeats(client, auth, monkeypatch):
    from app import worker
    from app.database import SessionLocal
    from app.models import Job, Segment, now

    monkeypatch.setenv("LLM_MAX_INPUT_CHARS", "1000")
    monkeypatch.setenv("LLM_REQUEST_INTERVAL_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Synthetic", "consent_confirmed": True}
    ).json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    with SessionLocal() as db:
        for i in range(5):
            db.add(
                Segment(
                    meeting_id=mid,
                    client_event_id=str(i),
                    text="Synthetic discussion. " * 12,
                    start_ms=i * 1000,
                    end_ms=(i + 1) * 1000,
                )
            )
        db.commit()
    queued = client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).json()
    calls = []
    failed = set()

    async def fake(settings, title, transcript, ids, gaps):
        calls.append(title)
        if " / section " in title and not title.endswith("section 1") and title not in failed:
            failed.add(title)
            raise ProviderError("Rate limited", retryable=True, retry_after=120)
        return {"title": title, "summary": "Saved section", "transcript_gaps": []}

    monkeypatch.setattr(worker, "llm_mom", fake)
    asyncio.run(worker.run_job(*worker.acquire_job()))
    with SessionLocal() as db:
        job = db.get(Job, queued["job_id"])
        assert job.status == "pending" and job.run_after is not None
        assert len(job.checkpoint["sections"]) == 1
        assert worker.acquire_job() is None
        job.run_after = now() - timedelta(seconds=1)
        db.commit()
    for _ in range(10):
        asyncio.run(worker.run_job(*worker.acquire_job()))
        with SessionLocal() as db:
            job = db.get(Job, queued["job_id"])
            if job.status == "completed":
                break
            assert job.status == "pending"
            job.run_after = now() - timedelta(seconds=1)
            db.commit()
    assert calls.count("Synthetic / section 1") == 1
    assert calls.count("Synthetic / section 2") == 2
    assert client.get(f"/api/meetings/{mid}/mom", headers=auth).status_code == 200
    job = client.get(f"/api/meetings/{mid}/jobs", headers=auth).json()[0]
    assert job["status"] == "completed" and job["error"] is None
    assert len(failed) == 4 and job["attempts"] == 5


@pytest.mark.parametrize("legacy", [False, True])
def test_expired_running_job_can_be_recovered_but_live_worker_keeps_lease(client, auth, legacy):
    from app import worker
    from app.database import SessionLocal
    from app.models import Job, now

    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Synthetic", "consent_confirmed": True}
    ).json()["id"]
    with SessionLocal() as db:
        db.add(Job(meeting_id=mid, kind="generate_mom"))
        db.commit()
    first = worker.acquire_job()
    assert first and worker.acquire_job() is None
    with SessionLocal() as db:
        job = db.get(Job, first[0])
        claim = job.claim_token
        job.lease_until = None if legacy else now() - timedelta(seconds=1)
        db.commit()
    assert worker.acquire_job()[0] == first[0]
    with SessionLocal() as db:
        assert db.get(Job, first[0]).claim_token != claim


def test_legacy_database_upgrade_preserves_records_and_is_idempotent(tmp_path):
    from app.database import initialize_database, make_engine

    path = tmp_path / "legacy.db"
    target = make_engine(f"sqlite:///{path}")
    with target.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY, meeting_id VARCHAR(36), kind VARCHAR(50), status VARCHAR(50), attempts INTEGER, error TEXT, created_at TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO jobs VALUES ('preserved', 'm', 'generate_mom', 'running', 1, NULL, CURRENT_TIMESTAMP)"
            )
        )
    initialize_database(target)
    initialize_database(target)
    assert {"run_after", "lease_until", "claim_token", "checkpoint"} <= {
        c["name"] for c in inspect(target).get_columns("jobs")
    }
    with target.connect() as connection:
        assert connection.execute(text("SELECT id FROM jobs")).scalar() == "preserved"
    assert len(list(tmp_path.glob("*.bak"))) == 1
    target.dispose()


def test_twelve_complete_meeting_histories_survive_new_database_connection(client, auth):
    from app.database import SessionLocal, engine
    from app.models import Mom, Segment

    mids = []
    for i in range(12):
        mid = client.post(
            "/api/meetings",
            headers=auth,
            json={"title": f"Synthetic meeting {i}", "consent_confirmed": True},
        ).json()["id"]
        mids.append(mid)
        with SessionLocal() as db:
            db.add(
                Segment(
                    meeting_id=mid,
                    client_event_id="event",
                    text=f"Transcript {i}",
                    start_ms=0,
                    end_ms=1000,
                )
            )
            db.add(
                Mom(
                    meeting_id=mid,
                    data={"title": f"Meeting {i}", "summary": f"Minutes {i}"},
                    status="draft",
                )
            )
            db.commit()
    engine.dispose()
    assert len(client.get("/api/meetings", headers=auth).json()) == 12
    for i, mid in enumerate(mids):
        assert (
            client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()["segments"][0][
                "text"
            ]
            == f"Transcript {i}"
        )
        assert (
            client.get(f"/api/meetings/{mid}/mom", headers=auth).json()["data"]["summary"]
            == f"Minutes {i}"
        )


def test_browser_codec_parameter_is_accepted_and_empty_generation_rejected(client, auth):
    import hashlib

    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Synthetic", "consent_confirmed": True}
    ).json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    payload = b"Synthetic codec-qualified recording"
    response = client.post(
        f"/api/meetings/{mid}/audio-chunks",
        headers=auth,
        data={
            "sequence": 0,
            "start_ms": 0,
            "end_ms": 1000,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        files={"audio": ("chunk.webm", payload, "audio/webm;codecs=opus")},
    )
    assert response.status_code == 201
    client.post(f"/api/meetings/{mid}/stop", headers=auth)
    assert client.post(f"/api/meetings/{mid}/mom/generate", headers=auth).status_code == 409


def test_capture_loss_remains_visible_after_stopping(client, auth):
    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Synthetic", "consent_confirmed": True}
    ).json()["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    client.post(
        f"/api/meetings/{mid}/capture-status",
        headers=auth,
        json={"capture_status": "active", "stt_status": "connected"},
    )
    client.post(
        f"/api/meetings/{mid}/capture-status",
        headers=auth,
        json={"capture_status": "lost", "stt_status": "disconnected"},
    )
    response = client.post(f"/api/meetings/{mid}/stop", headers=auth)
    assert response.json()["capture_status"] == "lost"


def test_diagnostics_excludes_keys_urls_and_meeting_content(client, monkeypatch):
    import json

    from app import diagnostics

    monkeypatch.setenv("LLM_API_KEY", "PRIVATE-SENTINEL")
    monkeypatch.setenv("LLM_ENDPOINT", "https://private-sentinel.example/v1")
    from app.config import get_settings

    get_settings.cache_clear()
    result = diagnostics.report()
    assert result["job_recovery_schema_ready"] is True
    assert result["saved_meetings"] == 0
    assert "PRIVATE-SENTINEL" not in json.dumps(result)
    assert "private-sentinel.example" not in json.dumps(result)


@pytest.mark.parametrize(
    "retryable,attempts,expected", [(True, 1, "pending"), (True, 3, "failed"), (False, 1, "failed")]
)
def test_retry_limits_and_configuration_errors_do_not_leave_running_jobs(
    client, auth, monkeypatch, retryable, attempts, expected
):
    from app import worker
    from app.database import SessionLocal
    from app.models import Job

    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Synthetic", "consent_confirmed": True}
    ).json()["id"]
    with SessionLocal() as db:
        item = Job(
            meeting_id=mid,
            kind="generate_mom",
            attempts=attempts - 1,
            checkpoint={"retry_count": attempts - 1},
        )
        db.add(item)
        db.commit()
        jid = item.id

    async def fail(*args):
        raise ProviderError("Synthetic safe failure", retryable=retryable, retry_after=90)

    monkeypatch.setattr(worker, "execute", fail)
    asyncio.run(worker.run_job(*worker.acquire_job()))
    with SessionLocal() as db:
        item = db.get(Job, jid)
        assert item.status == expected
        assert (item.run_after is not None) == (expected == "pending")


@pytest.mark.parametrize("same_content", [True, False])
def test_concurrent_audio_uploads_preserve_committed_checksum(client, auth, same_content):
    import asyncio
    import hashlib
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from fastapi import HTTPException
    from sqlalchemy import select

    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import AudioChunk
    from app.routes.audio import chunk

    mid = client.post(
        "/api/meetings",
        headers=auth,
        json={"title": "Concurrent upload", "consent_confirmed": True},
    ).json()["id"]
    assert client.post(f"/api/meetings/{mid}/start", headers=auth).status_code == 200
    barrier = threading.Barrier(2)
    settings = get_settings()

    def upload(payload):
        class Audio:
            content_type = "audio/wav"

            async def read(self, size):
                barrier.wait(timeout=10)
                return payload

        with SessionLocal() as db:
            try:
                result = asyncio.run(
                    chunk(
                        mid=mid,
                        sequence=0,
                        start_ms=0,
                        end_ms=1000,
                        sha256=hashlib.sha256(payload).hexdigest(),
                        audio=Audio(),
                        db=db,
                        actor="local-user",
                        settings=settings,
                    )
                )
                return result
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(upload, [b"first audio", b"first audio" if same_content else b"other audio"])
        )
    if same_content:
        assert sorted(result["duplicate"] for result in results) == [False, True]
        assert len({result["id"] for result in results}) == 1
    else:
        assert sum(result == 409 for result in results) == 1
    with SessionLocal() as db:
        rows = db.scalars(select(AudioChunk).where(AudioChunk.meeting_id == mid)).all()
        assert len(rows) == 1

        assert hashlib.sha256(storage_path(rows[0].path).read_bytes()).hexdigest() == rows[0].sha256
