import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.languages import AUTO_LANGUAGES
from app.providers import fast_transcribe_file


def test_meeting_defaults_to_auto_but_preserves_explicit_language(client, auth):
    for extra, expected in [({}, "auto"), ({"language": "mr-IN"}, "mr-IN")]:
        response = client.post(
            "/api/meetings",
            headers=auth,
            json={"title": "Languages", "consent_confirmed": True, **extra},
        )
        assert response.status_code == 201
        assert response.json()["language"] == expected


def test_optional_speaker_improvement_requests_diarization(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request.read().decode())
        return httpx.Response(200, json={"phrases": []})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw)
    )
    settings = Settings(
        _env_file=None,
        azure_speech_key="test",
        azure_speech_fast_endpoint="https://synthetic.invalid/transcribe",
    )
    asyncio.run(
        fast_transcribe_file(settings, b"RIFF", "meeting.wav", "audio/wav", diarization=True)
    )
    assert '"enabled": true' in requests[0] and '"maxSpeakers": 20' in requests[0]


@pytest.mark.parametrize("language", ["auto", "hi-IN", "mr-IN", "gu-IN", "fr-FR"])
def test_recovery_sends_real_locales_instead_of_auto_to_azure(monkeypatch, language):
    original = httpx.AsyncClient
    seen = []

    def respond(request):
        seen.append(request.read().decode())
        return httpx.Response(200, json={"phrases": []})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )
    settings = Settings(
        _env_file=None,
        azure_speech_key="synthetic",
        azure_speech_fast_endpoint="https://synthetic.invalid/transcribe",
    )
    asyncio.run(fast_transcribe_file(settings, b"RIFF", "test.wav", "audio/wav", language))
    locales = list(AUTO_LANGUAGES) if language == "auto" else [language]
    assert json.dumps({"locales": locales}) in seen[0]
    assert 1 <= len(AUTO_LANGUAGES) <= 10
    assert len({locale.split("-")[0] for locale in AUTO_LANGUAGES}) == len(AUTO_LANGUAGES)


def test_speech_credentials_advertise_the_same_languages_as_recovery(client, auth, monkeypatch):
    from app import main

    async def speech(_settings):
        return {"token": "synthetic", "region": "test", "expires_in_seconds": 600}

    monkeypatch.setattr(main, "azure_speech_token", speech)
    response = client.post("/api/speech/token", headers=auth)
    assert response.status_code == 200
    assert response.json()["auto_detect_languages"] == list(AUTO_LANGUAGES)


def test_native_scripts_round_trip_with_timestamps_and_speaker_ids(client, auth):
    meeting = client.post(
        "/api/meetings",
        headers=auth,
        json={"title": "Multilingual", "consent_confirmed": True},
    ).json()
    mid = meeting["id"]
    client.post(f"/api/meetings/{mid}/start", headers=auth)
    texts = [
        "आज की बैठक शुरू करते हैं।",
        "आजच्या बैठकीत आपण चर्चा करूया.",
        "આજની બેઠક શરૂ કરીએ.",
    ]
    segments = [
        {
            "client_event_id": f"native-{i}",
            "text": text,
            "speaker_id": f"live:synthetic:Guest-{i + 1}",
            "start_ms": i * 3000,
            "end_ms": i * 3000 + 2000,
        }
        for i, text in enumerate(texts)
    ]
    response = client.post(
        f"/api/meetings/{mid}/segments",
        headers=auth,
        json={"segments": segments},
    )
    assert response.status_code == 200
    stored = client.get(f"/api/meetings/{mid}/transcript", headers=auth).json()["segments"]
    assert [s["text"] for s in stored] == texts
    for original, saved in zip(segments, stored, strict=True):
        for field in ("start_ms", "end_ms", "speaker_id"):
            assert saved[field] == original[field]


def test_language_display_configuration_matches_backend_and_requires_auth(client, auth):
    response = client.get("/api/speech/languages", headers=auth)
    assert response.status_code == 200
    assert response.json() == {"auto_detect_languages": list(AUTO_LANGUAGES)}
    assert client.get("/api/speech/languages").status_code == 401
