import os,tempfile
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path}/test.db')
    monkeypatch.setenv('STORAGE_DIR',str(tmp_path/'storage'))
    monkeypatch.setenv('APP_API_TOKEN','test-token')
    monkeypatch.setenv('AZURE_SPEECH_KEY','')
    monkeypatch.setenv('AZURE_SPEECH_REGION','')
    from app.config import get_settings
    get_settings.cache_clear()
    # Source modules are freshly imported after test environment is in place.
    import importlib,sys
    for name in ('app.main','app.database','app.models'):
        sys.modules.pop(name,None)
    from app.main import app
    with TestClient(app) as c:yield c

@pytest.fixture
def auth():return {'Authorization':'Bearer test-token'}
