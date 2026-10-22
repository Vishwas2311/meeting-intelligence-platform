"""Backend-only contract, Oracle-query, maintenance and delivery regressions."""

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]


def test_every_success_has_a_schema_and_binary_is_not_json(client, auth):
    from scripts.check_api_contract import validate

    schema = client.get("/openapi.json").json()
    assert validate(schema) == 50
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/openapi.json", headers=auth).json() == schema
    for path, method, media in [
        ("/api/metrics", "get", "text/plain"),
        ("/api/storage/backup", "post", "application/zip"),
        ("/api/recaps/{mid}/audio", "get", "audio/*"),
    ]:
        content = schema["paths"][path][method]["responses"]["200"]["content"]
        assert media in content and "application/json" not in content
    assert "text/html" in schema["paths"]["/api/meetings"]["post"]["responses"]["429"]["content"]
    assert "304" in schema["paths"]["/api/meetings/{mid}/mom"]["get"]["responses"]


def test_minutes_edit_schema_describes_both_supported_inputs(client):
    from app.schemas import MomEdit

    schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert {s["$ref"].rsplit("/", 1)[1] for s in schema["MomEdit"]["anyOf"]} == {
        "MomEditEnvelope",
        "MomEditFlat",
    }
    assert schema["MomEditEnvelope"]["properties"]["data"]["$ref"].endswith("/MomData")
    for payload in [
        {"title": "Legacy", "summary": "Kept", "expected_revision": 2},
        {"data": {"title": "Legacy", "summary": "Kept"}, "expected_revision": 2},
    ]:
        assert MomEdit.model_validate(payload).expected_revision == 2
        assert MomEdit.model_validate(payload).minutes()["summary"] == "Kept"


def test_legacy_minutes_response_does_not_invent_optional_fields(client, auth):
    from app.database import SessionLocal
    from app.models import Mom

    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Legacy", "consent_confirmed": True}
    ).json()["id"]
    legacy = {"title": "Legacy", "summary": "Kept", "custom_provenance": {"version": 0}}
    with SessionLocal() as db:
        db.add(Mom(meeting_id=mid, data=legacy))
        db.commit()
    value = client.get(f"/api/meetings/{mid}/mom", headers=auth)
    assert value.status_code == 200 and value.json()["data"] == legacy
    assert value.headers["x-api-version"] == "1"
    assert client.get(f"/api/meetings/{mid}/snapshot", headers=auth).status_code == 200


def test_invalid_stored_response_is_not_leaked_through_exception_logs(client, auth, caplog):
    from app.database import SessionLocal
    from app.models import Mom
    from app.observability import log

    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Synthetic", "consent_confirmed": True}
    ).json()["id"]
    private = "NEVER-LOG-REJECTED-MINUTES"
    with SessionLocal() as db:
        db.add(Mom(meeting_id=mid, data={"title": "Synthetic", "summary": {"private": private}}))
        db.commit()
    log.addHandler(caplog.handler)
    try:
        response = client.get(f"/api/meetings/{mid}/mom", headers=auth)
        assert response.status_code == 500 and response.text == "Internal Server Error"
        assert "response.invalid" in caplog.text
        assert private not in caplog.text and private not in response.text
    finally:
        log.removeHandler(caplog.handler)


@pytest.mark.parametrize("size", [0, 1, 1001, 10000])
def test_oracle_in_lists_are_bounded(size):
    from sqlalchemy.dialects import oracle

    from app.models import Segment
    from app.query_helpers import ids_in

    statement = str(
        select(Segment.id)
        .where(ids_in(Segment.id, map(str, range(size))))
        .compile(dialect=oracle.dialect(), compile_kwargs={"literal_binds": True})
    )
    groups = re.findall(r"IN \(([^)]+)\)", statement)
    assert all(len(group.split(",")) <= 900 for group in groups)
    assert len(groups) == (size + 899) // 900


def test_recovered_transcript_over_1000_ids_keeps_paging_and_owner(client, auth):
    from app.database import SessionLocal
    from app.models import AuditEvent, Segment

    mid = client.post(
        "/api/meetings", headers=auth, json={"title": "Large recovery", "consent_confirmed": True}
    ).json()["id"]
    ids = [f"synthetic-{i:04d}" for i in range(1001)]
    with SessionLocal() as db:
        db.add_all(
            Segment(
                id=sid,
                meeting_id=mid,
                client_event_id=sid,
                text="Saved",
                start_ms=i,
                end_ms=i + 1,
                source="recovered",
            )
            for i, sid in enumerate(ids)
        )
        db.add(
            AuditEvent(
                meeting_id=mid,
                actor="system",
                action="transcript.recovered",
                detail={"segment_ids": ids, "loss_ids": []},
            )
        )
        db.commit()
    rows, after = [], None
    while True:
        page = client.get(
            f"/api/meetings/{mid}/transcript",
            headers=auth,
            params={"limit": 500, **({"after": after} if after else {})},
        ).json()
        rows.extend(s["id"] for s in page["segments"])
        after = page["next_cursor"]
        if not after:
            break
    assert rows == ids


def test_cleanup_drains_more_than_old_hourly_limit_without_touching_live_buckets(client):
    from app.database import SessionLocal
    from app.models import RateBucket, now
    from app.reconciliation import cleanup_rate_buckets

    with SessionLocal() as db:
        db.add_all(
            RateBucket(
                actor="cleanup-test",
                operation="expired",
                window=i,
                count=1,
                expires_at=now() - timedelta(days=1),
            )
            for i in range(5201)
        )
        db.add(
            RateBucket(
                actor="cleanup-test",
                operation="live",
                window=1,
                count=1,
                expires_at=now() + timedelta(hours=1),
            )
        )
        db.commit()
    removed = 0
    for _ in range(4):
        removed += cleanup_rate_buckets(time_budget=30)
    assert removed == 5201
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RateBucket)) == 1
        assert db.get(RateBucket, ("cleanup-test", "live", 1)).count == 1


def test_sql_and_admission_metrics_are_aggregates_not_queries(client, auth):
    client.get("/api/meetings?q=NEVER-LOG-SENSITIVE-TITLE", headers=auth)
    text = client.get("/api/metrics", headers=auth).text
    for name in [
        "meeting_sql_duration_seconds",
        "meeting_commit_duration_seconds",
        "meeting_admission_duration_seconds",
        "meeting_expired_rate_buckets",
    ]:
        assert name in text
    assert "NEVER-LOG" not in text and "test-token" not in text


def test_oracle_connect_timeout_is_explicit_without_opening_connection(client, monkeypatch):
    from app import database

    original, captured = database.create_engine, {}

    def capture(url, **kwargs):
        captured.update(kwargs)
        return original(url, **kwargs)

    monkeypatch.setattr(database, "create_engine", capture)
    engine = database.make_engine(
        "oracle+oracledb://synthetic:synthetic@/?dsn=synthetic.invalid/service"
    )
    try:
        assert captured["connect_args"]["tcp_connect_timeout"] == 5
        assert captured["pool_pre_ping"] is True
    finally:
        engine.dispose()


def test_production_can_disable_browser_keys_but_never_fall_back_to_sqlite(client):
    from pydantic import ValidationError

    from app.config import Settings

    values = dict(
        app_env="production",
        auth_mode="jwt",
        app_api_token="",
        api_client_keys={},
        public_origin="https://api.synthetic.invalid",
        policy_approved=True,
        usage_policy_version="test-v1",
        browser_recovery_enabled=False,
        local_cache_kek="",
        jwt_issuer="https://identity.synthetic.invalid",
        jwt_audience="synthetic",
        jwt_jwks_url="https://identity.synthetic.invalid/keys",
        database_url="oracle",
        oracle_user="synthetic",
        oracle_password="synthetic",
        oracle_dsn="synthetic.invalid/service",
        azure_speech_region="centralindia",
        azure_speech_key="synthetic",
        azure_speech_fast_endpoint="https://speech.synthetic.invalid",
        llm_endpoint="https://llm.synthetic.invalid",
        llm_model="synthetic",
        llm_api_key="synthetic",
    )
    assert Settings(_env_file=None, **values).database_url.startswith("oracle+oracledb:")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**values, "database_url": "sqlite:///:memory:"})


def test_database_report_never_connects_by_default(client, monkeypatch):
    from scripts.check_database import report

    def unexpected(*args, **kwargs):
        raise AssertionError("Default report must not create an engine or connect")

    monkeypatch.setattr("app.database.make_engine", unexpected)
    result = report()
    assert result["selected_backend"] == "sqlite" and result["oracle_connectivity"] == "NOT_RUN"
    assert "test-token" not in json.dumps(result)


def test_api_deployment_has_no_ui_or_redis_dependency():
    import yaml

    value = yaml.safe_load((ROOT / "deploy/compose.api.yaml").read_text())
    assert set(value["services"]) == {"api", "worker", "gateway"}
    assert value["services"]["worker"]["environment"]["DB_MAX_OVERFLOW"] == "0"
    assert "WEB_IMAGE" not in json.dumps(value) and "redis" not in json.dumps(value).lower()
    gateway = (ROOT / "deploy/nginx.api.conf").read_text()
    assert "location / { return 404; }" in gateway
    assert "index.html" not in gateway and "client_max_body_size 101m" in gateway
