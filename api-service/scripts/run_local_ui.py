"""Run this package's API, worker and copied UI; Ctrl+C stops only these children."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    node = os.getenv("NODE_EXE") or shutil.which("node")
    if not node or not Path(node).is_file():
        raise SystemExit("Install Node.js or set NODE_EXE to its executable path.")
    if not (ROOT / ".env").is_file():
        raise SystemExit("Create api-service/.env from .env.example before starting.")
    vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
    if not vite.is_file():
        raise SystemExit("Install copied frontend dependencies: cd frontend; npm ci")
    sys.path.insert(0, str(ROOT))
    from app.config import get_settings
    from app.database import initialize_database

    settings = get_settings()
    if settings.auth_mode != "api_key" or settings.api_client_keys or not settings.app_api_token:
        raise SystemExit(
            "Local UI requires AUTH_MODE=api_key, API_CLIENT_KEYS={} and APP_API_TOKEN. JWT login is a separate integration."
        )
    for port in [8100, 5174]:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(
                    f"Port {port} is occupied; stop the existing service before starting."
                ) from None
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    initialize_database()
    commands = [
        (
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8100",
            ],
            ROOT,
        ),
        ([sys.executable, "-m", "app.worker"], ROOT),
        ([node, str(vite)], ROOT / "frontend"),
    ]
    children = []
    try:
        for command, cwd in commands:
            children.append(subprocess.Popen(command, cwd=cwd))
        print(
            "API service UI: http://127.0.0.1:5174 | API: http://127.0.0.1:8100 | Ctrl+C stops this group.",
            flush=True,
        )
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("An API-service process stopped; the group is shutting down.")
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    main()
