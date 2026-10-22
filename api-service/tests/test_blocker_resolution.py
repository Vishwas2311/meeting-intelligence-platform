"""Focused production-blocker regressions; no external services or UI."""

import asyncio

import anyio
import httpx


def test_liveness_survives_exhausted_sync_thread_limiter(client):
    from app.main import app

    async def check():
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = 1
        borrower = object()
        await limiter.acquire_on_behalf_of(borrower)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                response = await asyncio.wait_for(api.get("/api/health"), timeout=1)
                assert response.status_code == 200 and response.json() == {"status": "alive"}
        finally:
            limiter.release_on_behalf_of(borrower)

    asyncio.run(check())


def test_database_failure_is_visible_without_logging_private_error(
    client, auth, monkeypatch, caplog
):
    from sqlalchemy.exc import OperationalError

    from app.observability import log

    def unavailable(*args):
        raise OperationalError("PRIVATE-SQL", {}, RuntimeError("PRIVATE-DSN"))

    monkeypatch.setattr("app.limits.admit_request", unavailable)
    log.addHandler(caplog.handler)
    try:
        response = client.get("/api/meetings", headers={**auth, "X-Request-ID": "db-failure"})
        assert response.status_code == 503 and response.headers["retry-after"] == "5"
        assert '"event":"database.unavailable"' in caplog.text
        assert '"error_type":"OperationalError"' in caplog.text
        assert "PRIVATE" not in caplog.text + response.text
    finally:
        log.removeHandler(caplog.handler)


def test_load_timing_summary():
    from load.local_smoke import timing_summary

    assert timing_summary([100, 1, 2, 3])["max_ms"] == 100
    assert timing_summary([2])["p95_ms"] == 2


def test_httpcore_async_detection_uses_installed_sniffio(monkeypatch):
    import sniffio
    from httpcore._synchronization import current_async_library

    calls = []
    monkeypatch.setattr(sniffio, "current_async_library", lambda: calls.append(True) or "asyncio")
    assert current_async_library() == "asyncio" and calls == [True]


def test_admission_waiters_do_not_fill_endpoint_thread_pool(client, auth, monkeypatch):
    import threading
    import time

    from app.config import get_settings
    from app.main import app

    active = peak = 0
    lock = threading.Lock()

    def admission(*args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1

    monkeypatch.setattr("app.limits.admit_request", admission)

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            responses = await asyncio.gather(
                *(api.get("/api/auth/me", headers=auth) for _ in range(25))
            )
            assert all(response.status_code == 200 for response in responses)

    asyncio.run(check())
    settings = get_settings()
    assert 0 < peak <= max(1, (settings.db_pool_size + settings.db_max_overflow) // 2)


def test_unknown_jwt_keys_do_not_amplify_jwks_fetches(monkeypatch):
    import io
    import json
    from types import SimpleNamespace

    import jwt
    import pytest
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.security import signing_keys

    public = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public))
    key.update(kid="known", use="sig", alg="RS256")
    fetches = []

    def fetch(*args, **kwargs):
        fetches.append(True)
        return io.BytesIO(json.dumps({"keys": [key]}).encode())

    monkeypatch.setattr(
        "jwt.jwks_client.urllib.request.build_opener", lambda *args: SimpleNamespace(open=fetch)
    )
    signing_keys.cache_clear()
    try:
        keys = signing_keys("https://identity.synthetic.invalid/keys")
        assert keys.get_signing_key("known").key_id == "known"
        for index in range(20):
            with pytest.raises(jwt.PyJWKClientError):
                keys.get_signing_key(f"unknown-{index}")
        assert len(fetches) == 1
    finally:
        signing_keys.cache_clear()


def test_oracle_preflight_rejects_wrong_schema_head(client, monkeypatch, capsys):
    import runpy
    import sys
    from types import SimpleNamespace

    import pytest

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def exec_driver_sql(self, statement):
            assert statement == "SET TRANSACTION READ ONLY"

        def scalar(self, statement):
            return "old_schema" if "alembic_version" in str(statement) else 1

    settings = SimpleNamespace(app_env="production", database_url="oracle+oracledb://")
    engine = SimpleNamespace(
        connect=Connection, dialect=SimpleNamespace(server_version_info=(19,)), dispose=lambda: None
    )
    with monkeypatch.context() as patch:
        patch.setattr("app.config.get_settings", lambda: settings)
        patch.setattr("app.database.engine", engine)
        patch.setattr(sys, "argv", ["check_database.py", "--connect"])
        with pytest.raises(SystemExit) as result:
            runpy.run_path("scripts/check_database.py", run_name="__main__")
        assert result.value.code == 1
    assert '"schema_head_matches": false' in capsys.readouterr().out
