import json
from pathlib import Path

import pytest


def test_published_payloads_follow_actual_schema():
    from app.recaps import RecapCreate, RecapNotes
    from app.schemas import (
        Approval,
        CaptureUpdate,
        Manifest,
        MeetingCreate,
        MomEdit,
        RevisionRequest,
        SegmentBatch,
        SpeakerRename,
    )

    examples = json.loads(
        (Path(__file__).resolve().parents[1] / "examples/requests.json").read_text()
    )
    for name, schema in {
        "meeting_create": MeetingCreate,
        "segments": SegmentBatch,
        "manifest": Manifest,
        "capture_status": CaptureUpdate,
        "speaker_rename": SpeakerRename,
        "notes_create": RecapCreate,
        "notes_patch": RecapNotes,
        "minutes_edit": MomEdit,
        "minutes_approve": Approval,
        "minutes_revise": RevisionRequest,
    }.items():
        schema.model_validate(examples[name])


def test_invalid_minutes_envelopes_are_rejected_as_input_errors(client, auth):
    for value in [None, [], "not an object", 42]:
        response = client.patch(
            "/api/meetings/synthetic/mom",
            headers=auth,
            json={"expected_revision": 0, "data": value},
        )
        assert response.status_code == 422


def test_schema_publishes_bearer_and_identity_route(client):
    schema = client.get("/openapi.json").json()
    assert "/api/auth/me" in schema["paths"]
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/api/meetings"]["post"]["security"] == [{"HTTPBearer": []}]


def test_unknown_ownership_fields_rejected(client, auth):
    response = client.post(
        "/api/meetings",
        headers=auth,
        json={
            "title": "Invalid payload",
            "consent_confirmed": True,
            "created_by": "someone-else",
        },
    )
    assert response.status_code == 422


def test_documented_notes_to_mom_flow(client, auth, monkeypatch):
    from app import worker

    payloads = json.loads(
        (Path(__file__).resolve().parents[1] / "examples/requests.json").read_text()
    )
    created = client.post("/api/recaps", headers=auth, json=payloads["notes_create"])
    assert created.status_code == 201
    mid = created.json()["id"]
    notes = {**payloads["notes_patch"], "reviewed": True}
    assert client.patch(f"/api/recaps/{mid}/notes", headers=auth, json=notes).status_code == 200

    async def synthetic(settings, title, text, ids, gaps):
        return {"title": title, "summary": "Synthetic contract test summary", "transcript_gaps": []}

    monkeypatch.setattr(worker, "llm_mom", synthetic)
    queued = client.post(f"/api/meetings/{mid}/mom/generate", headers=auth)
    assert queued.status_code == 202
    import asyncio

    asyncio.run(worker.run_job(*worker.acquire_job()))
    assert client.get(f"/api/meetings/{mid}/jobs", headers=auth).json()[0]["status"] == "completed"
    assert client.get(f"/api/meetings/{mid}/mom", headers=auth).status_code == 200


@pytest.mark.parametrize(
    "origin,allowed", [("https://ui.example.test", True), ("https://other.example.test", False)]
)
def test_cors_requires_configured_origin(client, origin, allowed):
    response = client.options(
        "/api/meetings",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert (response.status_code == 200) == allowed


def test_published_openapi_matches_running_service(client):
    published = Path(__file__).resolve().parents[1] / "docs/guides/openapi.json"
    assert json.loads(published.read_text(encoding="utf-8")) == client.get("/openapi.json").json()
