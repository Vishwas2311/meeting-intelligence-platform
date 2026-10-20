"""JSON-lines transport for browser tests: real ASGI API, no network listener or real data."""

import base64
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with TemporaryDirectory(prefix="meeting-ui-test-") as temporary:
    os.environ.update(
        MEETING_UI_TEST="1",
        DATABASE_URL=f"sqlite:///{Path(temporary).as_posix()}/test.db",
        STORAGE_DIR=str(Path(temporary) / "audio"),
        APP_API_TOKEN="synthetic-ui-test-token",
        AZURE_SPEECH_KEY="",
        AZURE_SPEECH_KEY_SECONDARY="",
        AZURE_SPEECH_REGION="",
        AZURE_SPEECH_FAST_ENDPOINT="https://synthetic.invalid/transcribe",
        LLM_ENDPOINT="",
        LLM_API_KEY="",
        LLM_MODEL="",
        LLM_REQUEST_INTERVAL_SECONDS="0",
    )
    from fastapi.testclient import TestClient
    from ui_server import app, engine

    try:
        with TestClient(app) as client:
            print(json.dumps({"ready": True}), flush=True)
            for line in sys.stdin:
                message = json.loads(line)
                path = message["path"]
                assert path.startswith(("/api/", "/__test__/"))
                response = client.request(
                    message.get("method", "GET"),
                    path,
                    headers={
                        "Authorization": "Bearer synthetic-ui-test-token",
                        **message.get("headers", {}),
                    },
                    content=base64.b64decode(message.get("body", "")),
                )
                print(
                    json.dumps(
                        {
                            "id": message["id"],
                            "status": response.status_code,
                            "contentType": response.headers.get("content-type", ""),
                            "body": base64.b64encode(response.content).decode(),
                        }
                    ),
                    flush=True,
                )
    finally:
        engine.dispose()
