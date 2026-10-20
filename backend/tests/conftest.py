import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("APP_API_TOKEN", "test-token")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "")
    monkeypatch.setenv("AZURE_SPEECH_KEY_SECONDARY", "")
    monkeypatch.setenv("LLM_ENDPOINT", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_REQUEST_INTERVAL_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    # Source modules are freshly imported after test environment is in place.
    import sys

    for name in ("app.main", "app.worker", "app.database", "app.models"):
        sys.modules.pop(name, None)
        vars(sys.modules["app"]).pop(name.removeprefix("app."), None)
    from app.main import app

    with TestClient(app) as c:
        yield c
    from app.database import engine

    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}
