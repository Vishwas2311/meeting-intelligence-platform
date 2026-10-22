import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("APP_API_TOKEN", "test-token")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("API_CLIENT_KEYS", "{}")
    monkeypatch.setenv("API_ADMIN_CLIENTS", '["local-user"]')
    monkeypatch.setenv("CORS_ORIGINS", "https://ui.example.test")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "")
    monkeypatch.setenv("AZURE_SPEECH_KEY_SECONDARY", "")
    monkeypatch.setenv("AZURE_SPEECH_FAST_ENDPOINT", "https://synthetic.invalid/transcribe")
    monkeypatch.setenv("LLM_ENDPOINT", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_REQUEST_INTERVAL_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    # Keep provider decorators and admission together so worker ContextVars stay identical.
    # Other source modules are freshly imported after the isolated environment is in place.
    import sys

    for name in (
        "app.routes.meetings",
        "app.routes.minutes",
        "app.routes.transcripts",
        "app.routes.audio",
        "app.routes.speech",
        "app.routes.system",
        "app.routes",
        "app.api_common",
        "app.mutations",
        "app.limits",
        "app.local_cache",
        "app.audio_store",
        "app.request_limits",
        "app.observability",
        "app.routes.client_events",
        "app.main",
        "app.worker",
        "app.minutes_workflow",
        "app.recaps",
        "app.improvements",
        "app.database",
        "app.models",
    ):
        sys.modules.pop(name, None)
        parent, _, attribute = name.rpartition(".")
        if parent in sys.modules:
            vars(sys.modules[parent]).pop(attribute, None)
    from app.main import app

    with TestClient(app) as c:
        yield c
    from app.database import engine

    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}
