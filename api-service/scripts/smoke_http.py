"""Real HTTP API + independent worker smoke test with a local synthetic LLM endpoint."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


class SyntheticProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data = {
            "title": "Synthetic HTTP test",
            "summary": "Local synthetic provider response.",
            "discussion_points": [],
            "decisions": [],
            "action_items": [],
            "risks": [],
            "unanswered_questions": [],
            "transcript_gaps": [],
            "requires_review": True,
        }
        body = json.dumps(
            {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def run():
    with tempfile.TemporaryDirectory(prefix="meeting-api-smoke-") as folder:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        provider = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticProvider)
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        env = {
            **os.environ,
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{Path(folder).as_posix()}/service.db",
            "STORAGE_DIR": str(Path(folder) / "audio"),
            "AUTH_MODE": "api_key",
            "API_CLIENT_KEYS": json.dumps({"smoke": "synthetic-smoke-token-" * 3}),
            "API_ADMIN_CLIENTS": "[]",
            "APP_API_TOKEN": "",
            "CORS_ORIGINS": "",
            "AZURE_SPEECH_KEY": "",
            "AZURE_SPEECH_KEY_SECONDARY": "",
            "AZURE_SPEECH_FAST_ENDPOINT": "",
            "AZURE_SPEECH_REGION": "",
            "LLM_ENDPOINT": f"http://127.0.0.1:{provider.server_port}/chat/completions",
            "LLM_API_KEY": "synthetic-local-provider",
            "LLM_MODEL": "synthetic",
            "LLM_REQUEST_INTERVAL_SECONDS": "0",
        }
        children = []
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            for args in [
                ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                ["-m", "app.worker"],
            ]:
                children.append(
                    subprocess.Popen(
                        [sys.executable, *args],
                        cwd=ROOT,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=flags,
                    )
                )
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=5,
                headers={"Authorization": "Bearer " + "synthetic-smoke-token-" * 3},
            ) as c:
                deadline = time.monotonic() + 40
                while True:
                    if any(child.poll() is not None for child in children):
                        raise RuntimeError("Smoke API/worker process exited unexpectedly")
                    try:
                        ready = c.get("/api/ready")
                        if ready.status_code == 200 and ready.json()["worker"]:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError("API/worker readiness deadline reached")
                    time.sleep(0.2)
                assert c.get("/api/auth/me").json()["actor"] == "client:smoke"
                mid = str(uuid4())
                created = c.post("/api/recaps", json={"id": mid, "title": "Synthetic HTTP notes"})
                assert created.status_code == 201, created.text
                saved = c.patch(
                    f"/api/recaps/{mid}/notes",
                    json={
                        "notes": "We discussed the delivery plan.",
                        "reviewed": True,
                        "revision": 0,
                    },
                )
                assert saved.status_code == 200, saved.text
                queued = c.post(f"/api/meetings/{mid}/mom/generate")
                assert queued.status_code == 202, queued.text
                jid = queued.json()["job_id"]
                deadline = time.monotonic() + 30
                while True:
                    job = next(
                        j for j in c.get(f"/api/meetings/{mid}/jobs").json() if j["id"] == jid
                    )
                    assert job["status"] != "failed", job
                    if job["status"] == "completed":
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("Worker processing deadline reached")
                    time.sleep(0.2)
                minutes = c.get(f"/api/meetings/{mid}/mom")
                assert minutes.status_code == 200
                assert minutes.json()["data"]["summary"] == "Local synthetic provider response."
                assert c.get("/api/storage").status_code == 403
                summary = {
                    "transport": "real loopback HTTP",
                    "worker": "separate process",
                    "authentication": "service API key",
                    "provider": "local synthetic HTTP server",
                    "notes_to_mom": "passed",
                    "global_admin_restriction": "passed",
                }
                (ROOT / "docs/test-results/http-smoke-result.json").write_text(
                    json.dumps(summary, indent=2) + "\n", encoding="utf-8"
                )
                print(json.dumps(summary))
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    run()
