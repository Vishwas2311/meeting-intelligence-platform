import hashlib
from datetime import timedelta

import pytest


def test_security_denial_events_are_correlated_and_library_urls_are_suppressed(client, caplog):
    import logging

    from app.observability import event, log

    log.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO):
            response = client.get("/api/meetings", headers={"X-Request-ID": "synthetic-denial"})
            logging.getLogger("httpx").info("https://private-provider.invalid/secret-query")
            event("synthetic.safe", token="NEVER-LOG-THIS", request_id="synthetic-denial")
        assert response.status_code == 401
        assert '"event":"security.denied"' in caplog.text
        assert '"request_id":"synthetic-denial"' in caplog.text
        assert "private-provider" not in caplog.text and "NEVER-LOG-THIS" not in caplog.text
    finally:
        log.removeHandler(caplog.handler)


def test_recovery_manifest_rejects_tampering_and_escape(tmp_path):
    from scripts.verify_recovery_manifest import verify

    data = b"synthetic audio"
    path = tmp_path / "part.bin"
    path.write_bytes(data)
    manifest = {
        "audio": [
            {"key": "part.bin", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        ]
    }
    assert verify(manifest, tmp_path) == 1
    path.write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="checksum"):
        verify(manifest, tmp_path)
    manifest["audio"][0]["key"] = "../outside.bin"
    with pytest.raises(RuntimeError, match="Unsafe"):
        verify(manifest, tmp_path)


def test_metrics_and_client_events_are_restricted_and_redacted(client, auth):
    response = client.get("/api/metrics", headers=auth)
    assert response.status_code == 200
    assert "meeting_live_workers" in response.text
    assert "meeting_jobs" in response.text
    assert "test-token" not in response.text
    assert client.get("/api/metrics").status_code == 401
    assert (
        client.post(
            "/api/client-events", headers=auth, json={"kind": "local_storage_failed"}
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/client-events",
            headers=auth,
            json={"kind": "local_storage_failed", "text": "PRIVATE"},
        ).status_code
        == 422
    )


def test_missing_production_mount_marker_fails_closed(client, tmp_path):
    from types import SimpleNamespace

    from app.audio_store import check_volume

    settings = SimpleNamespace(
        storage_dir=tmp_path, app_env="production", storage_mount_marker=".meeting-volume"
    )
    with pytest.raises(RuntimeError, match="not mounted"):
        check_volume(settings)
    (tmp_path / ".meeting-volume").touch()
    check_volume(settings)


def test_expired_pending_upload_is_quarantined_and_quota_released(client, auth):
    from io import BytesIO

    from app.audio_store import reserve, write_reserved
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import ActorUsage, UploadReservation, now
    from app.mutations import lock_meeting
    from app.reconciliation import reconcile_uploads

    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Synthetic", "consent_confirmed": True}
    ).json()["id"]
    with SessionLocal() as db:
        item = reserve(db, lock_meeting(db, mid), "0", 4, get_settings())
        write_reserved(item, BytesIO(b"test"), 4)
        item.expires_at = now() - timedelta(seconds=1)
        db.commit()
        rid = item.id
    assert reconcile_uploads()["expired"] == 1
    assert reconcile_uploads(True)["released"] == 1
    assert reconcile_uploads(True)["released"] == 0
    with SessionLocal() as db:
        assert db.get(ActorUsage, "local-user").reserved_bytes == 0
        assert db.get(UploadReservation, rid).state == "reconciled"
    assert len(list((get_settings().storage_dir / ".quarantine").iterdir())) == 1


def test_pagination_and_owner_etag_contract(client, auth):
    mids = [
        client.post(
            "/api/meetings",
            headers=auth,
            json={"title": f"Synthetic {i}", "consent_confirmed": True},
        ).json()["id"]
        for i in range(3)
    ]
    first = client.get("/api/meetings?limit=2", headers=auth).json()
    client.post(
        "/api/meetings", headers=auth, json={"title": "New insert", "consent_confirmed": True}
    )
    second = client.get("/api/meetings?limit=2&after=" + first[-1]["id"], headers=auth).json()
    assert len(second) == 1 and second[0]["id"] == mids[0]
    mid = mids[0]
    page = client.get(f"/api/meetings/{mid}/transcript", headers=auth)
    assert (
        client.get(
            f"/api/meetings/{mid}/transcript",
            headers={**auth, "If-None-Match": page.headers["etag"]},
        ).status_code
        == 304
    )


def test_production_configuration_requires_oracle_identity_policy_and_wrapping_key(
    client, monkeypatch, tmp_path
):
    import base64

    from pydantic import ValidationError

    from app.config import Settings, get_settings

    values = dict(
        app_env="production",
        auth_mode="jwt",
        app_api_token="",
        api_client_keys={},
        public_origin="https://meetings.example.test",
        policy_approved=True,
        usage_policy_version="test-v1",
        local_cache_kek=base64.b64encode(b"k" * 32).decode(),
        jwt_issuer="https://id.example.test",
        jwt_jwks_url="https://id.example.test/keys",
        jwt_audience="synthetic",
        database_url="oracle",
        oracle_user="synthetic",
        oracle_password="synthetic-only",
        oracle_dsn="synthetic.invalid/db",
        azure_speech_region="centralindia",
        azure_speech_key="synthetic-only",
        azure_speech_fast_endpoint="https://speech.invalid/transcribe",
        llm_endpoint="https://model.invalid/chat",
        llm_model="synthetic",
        llm_api_key="synthetic-only",
    )
    assert Settings(_env_file=None, **values).app_env == "production"
    for overrides in [
        dict(policy_approved=False),
        dict(database_url="sqlite:///:memory:"),
        dict(auth_mode="api_key"),
        dict(local_cache_kek="PRIVATE-INVALID"),
        dict(llm_endpoint="http://model.invalid"),
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{**values, **overrides})
    secret = tmp_path / "secret"
    secret.write_text("synthetic-secret-file")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY_FILE", str(secret))
    get_settings.cache_clear()
    assert get_settings().llm_api_key == "synthetic-secret-file"
    monkeypatch.setenv("LLM_API_KEY", "PRIVATE-CONFLICT")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert "PRIVATE" not in str(error.value)
