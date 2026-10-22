"""Exercise the built backend image with isolated Docker data and synthetic providers."""

import hashlib
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from uuid import uuid4

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.getenv("SMOKE_BACKEND_IMAGE", "meeting-api-backend:test")


def command(args, timeout=900, **kwargs):
    result = subprocess.run(
        args, cwd=ROOT, text=True, capture_output=True, timeout=timeout, **kwargs
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-5000:] + result.stdout[-5000:])
    return result.stdout


def run():
    project = "meeting-backend-test-" + uuid4().hex[:10]
    test_container = project + "-tests"
    # Load structure without reading the private .env; inject only isolated synthetic inputs.
    config = yaml.safe_load((ROOT / "compose.yaml").read_text())
    config["name"] = project
    config["volumes"] = {"service-data": {}}
    config["networks"] = {"default": {}}
    token = secrets.token_urlsafe(48)
    env = {
        "DATABASE_URL": "sqlite:////srv/service/var/meeting_intelligence.db",
        "STORAGE_DIR": "/srv/service/var/audio",
        "AUTH_MODE": "api_key",
        "API_CLIENT_KEYS": json.dumps({"smoke": token}),
        "API_ADMIN_CLIENTS": "[]",
        "APP_API_TOKEN": "",
        "CORS_ORIGINS": "",
        "LLM_ENDPOINT": "http://provider:8080/chat/completions",
        "LLM_API_KEY": "synthetic",
        "LLM_MODEL": "synthetic",
        "LLM_REQUEST_INTERVAL_SECONDS": "0",
        "AZURE_SPEECH_KEY": "",
        "AZURE_SPEECH_REGION": "",
    }
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    for service in config["services"].values():
        service.pop("build", None)
        service.pop("env_file", None)
        service["image"] = IMAGE
        service["environment"] = env.copy()
    config["services"]["api"]["ports"] = [f"127.0.0.1:{port}:8100"]
    config["services"]["provider"] = {
        "image": IMAGE,
        "volumes": [
            {
                "type": "bind",
                "source": str(ROOT / "scripts/smoke_http.py"),
                "target": "/srv/service/scripts/smoke_http.py",
                "read_only": True,
            }
        ],
        "command": [
            "python",
            "-c",
            "import runpy; m=runpy.run_path('/srv/service/scripts/smoke_http.py'); m['ThreadingHTTPServer'](('0.0.0.0',8080),m['SyntheticProvider']).serve_forever()",
        ],
    }
    with tempfile.TemporaryDirectory(prefix="meeting-docker-test-") as folder:
        target = Path(folder)
        recipe = target / "compose.json"
        recipe.write_text(json.dumps(config), encoding="utf-8")
        compose = ["docker", "compose", "-p", project, "-f", str(recipe)]
        summary = {}
        try:
            print("Starting isolated API, worker and synthetic provider containers.", flush=True)
            command([*compose, "up", "-d", "--wait", "--wait-timeout", "90"])
            assert command([*compose, "exec", "-T", "api", "id", "-u"]).strip() == "10001"
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=10,
                headers={"Authorization": "Bearer " + token},
            ) as client:

                def wait_ready():
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        try:
                            response = client.get("/api/ready")
                            if response.status_code == 200 and response.json()["worker"]:
                                return
                        except httpx.TransportError:
                            pass
                        time.sleep(0.5)
                    raise TimeoutError("Container worker readiness deadline reached")

                def generate(mid):
                    queued = client.post(f"/api/meetings/{mid}/mom/generate")
                    assert queued.status_code == 202, queued.text
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        jobs = client.get(f"/api/meetings/{mid}/jobs").json()
                        job = next(j for j in jobs if j["id"] == queued.json()["job_id"])
                        assert job["status"] != "failed", job
                        if job["status"] == "completed":
                            assert client.get(f"/api/meetings/{mid}/mom").status_code == 200
                            return
                        time.sleep(0.3)
                    raise TimeoutError("Container generation deadline reached")

                wait_ready()
                assert client.get("/api/auth/me").json()["actor"] == "client:smoke"
                assert (
                    client.get(
                        "/api/meetings", headers={"Authorization": "Bearer invalid"}
                    ).status_code
                    == 401
                )
                assert client.get("/api/storage").status_code == 403
                notes = client.post(
                    "/api/recaps", json={"id": str(uuid4()), "title": "Docker notes"}
                )
                assert notes.status_code == 201, notes.text
                nid = notes.json()["id"]
                assert (
                    client.patch(
                        f"/api/recaps/{nid}/notes",
                        json={
                            "notes": "We discussed the delivery plan.",
                            "reviewed": True,
                            "revision": 0,
                        },
                    ).status_code
                    == 200
                )
                generate(nid)
                created = client.post(
                    "/api/meetings", json={"title": "Docker meeting", "consent_confirmed": True}
                )
                assert created.status_code == 201, created.text
                mid = created.json()["id"]
                assert client.post(f"/api/meetings/{mid}/start").status_code == 200
                stream = io.BytesIO()
                with wave.open(stream, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(16000)
                    audio.writeframes(b"\0" * 32000)
                payload = stream.getvalue()
                form = {
                    "sequence": 0,
                    "start_ms": 0,
                    "end_ms": 1000,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for duplicate in (False, True):
                    uploaded = client.post(
                        f"/api/meetings/{mid}/audio-chunks",
                        data=form,
                        files={"audio": ("test.wav", payload, "audio/wav")},
                    )
                    assert uploaded.status_code == 201, uploaded.text
                    assert uploaded.json()["duplicate"] is duplicate
                segments = client.post(
                    f"/api/meetings/{mid}/segments",
                    json={
                        "segments": [
                            {
                                "client_event_id": "docker-one",
                                "text": "We discussed delivery.",
                                "start_ms": 0,
                                "end_ms": 1000,
                                "speaker_id": "speaker-1",
                            }
                        ]
                    },
                )
                assert segments.status_code in (200, 201), segments.text
                assert (
                    client.post(
                        f"/api/meetings/{mid}/manifest",
                        json={
                            "expected_chunks": 1,
                            "recorded_through_ms": 1000,
                            "finalized": True,
                            "pauses": [],
                        },
                    ).status_code
                    == 200
                )
                assert client.get(f"/api/meetings/{mid}/coverage").json()[
                    "audio_capture_claimed_complete"
                ]
                assert client.post(f"/api/meetings/{mid}/stop").status_code == 200
                generate(mid)
                assert (
                    client.post(
                        f"/api/meetings/{mid}/mom/approve", json={"approved": True}
                    ).status_code
                    == 200
                )
                command([*compose, "restart", "api", "worker"])
                wait_ready()
                assert client.get(f"/api/meetings/{mid}/mom").json()["status"] == "approved"
                assert (
                    client.get(f"/api/meetings/{mid}/transcript").json()["segments"][0][
                        "speaker_id"
                    ]
                    == "speaker-1"
                )
                assert client.get(f"/api/meetings/{nid}/mom").status_code == 200
                summary = {
                    "runtime": "Docker Linux image",
                    "non_root_uid": 10001,
                    "notes_to_mom": "passed",
                    "meeting_audio_to_mom": "passed",
                    "duplicate_upload": "passed",
                    "authentication": "passed",
                    "approval_and_restart_persistence": "passed",
                    "provider": "synthetic HTTP container; no cloud calls",
                }
            print("Running the full backend suite inside the built Linux image.", flush=True)
            # Unit fixtures are ephemeral; real restart persistence is checked on the named volume above.
            args = [
                "docker",
                "run",
                "--rm",
                "--name",
                test_container,
                "--user",
                "0",
                "--tmpfs",
                "/tmp:size=2147483648,mode=1777",
            ]
            for name in (
                "tests",
                "scripts",
                "examples",
                "docs",
                "deploy",
                "load",
                "compose.yaml",
                "README.md",
                "pyproject.toml",
                "requirements-dev.txt",
            ):
                args += [
                    "--mount",
                    f"type=bind,source={ROOT / name},target=/srv/service/{name},readonly",
                ]
            wheels = os.getenv("SMOKE_QA_WHEELS")
            tools = os.getenv("SMOKE_QA_TOOLS")
            lint = " && python -m ruff check app scripts tests examples"
            install = "pip install --quiet --timeout 60 --retries 2 -r requirements-dev.txt"
            if wheels:
                wheel_directory = Path(wheels).resolve()
                if not wheel_directory.is_dir():
                    raise RuntimeError("QA wheel directory is unavailable")
                args += [
                    "--mount",
                    f"type=bind,source={wheel_directory},target=/qa-wheels,readonly",
                ]
                install = (
                    "pip install --quiet --no-index --find-links=/qa-wheels -r requirements-dev.txt"
                )
            if tools:
                tool_directory = Path(tools).resolve()
                if not (tool_directory / "pytest").is_dir():
                    raise RuntimeError("Offline pure-Python QA tools are unavailable")
                args += [
                    "--mount",
                    f"type=bind,source={tool_directory},target=/qa-tools,readonly",
                    "-e",
                    "PYTHONPATH=/qa-tools",
                ]
                install = "python -c \"import pytest; assert pytest.__version__ == '9.1.1'\""
                # Ruff is native; do not copy a Windows binary into the Linux test runner.
                print(
                    command(
                        [
                            sys.executable,
                            "-m",
                            "ruff",
                            "check",
                            "app",
                            "scripts",
                            "tests",
                            "examples",
                        ]
                    ),
                    flush=True,
                )
                lint = ""
            args += [
                "--mount",
                f"type=bind,source={ROOT.parent / '.github'},target=/repository-support/.github,readonly",
                "--mount",
                f"type=bind,source={target},target=/evidence",
                IMAGE,
                "sh",
                "-c",
                # Materialize read-only inputs once: repeated Windows bind-mount imports dominate tests.
                install
                + " && cp -a /srv/service /tmp/meeting-qa && cp -a /repository-support/. /tmp/ && cd /tmp/meeting-qa && python -m pytest -q --junitxml=/evidence/docker-test-results.xml && python -m pip check"
                + lint,
            ]
            try:
                print(command(args, timeout=2700), flush=True)
            finally:
                evidence = target / "docker-test-results.xml"
                if evidence.exists():
                    (ROOT / "docs/test-results/docker-test-results.xml").write_bytes(
                        evidence.read_bytes()
                    )
            (ROOT / "docs/test-results/docker-smoke-result.json").write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(summary), flush=True)
        except Exception:
            print(command([*compose, "logs", "--no-color", "--tail", "30"]), flush=True)
            raise
        finally:
            subprocess.run(
                ["docker", "rm", "-f", test_container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            command([*compose, "down", "--volumes", "--remove-orphans"])


if __name__ == "__main__":
    run()
