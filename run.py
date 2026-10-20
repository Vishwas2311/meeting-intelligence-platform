"""Run API, worker and UI together. Ctrl+C or `python run.py stop` stops this supervisor."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))


def acquire_lock(path):
    handle = path.open("a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError(
            "This application is already supervised. Use run.py stop first."
        ) from None
    return handle


def ensure_free_ports():
    import socket

    for port in (8000, 5173):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError(
                    f"Port {port} is already in use. Stop that app before starting a second copy."
                ) from None


def update_environment(lines, values):
    result, seen = [], set()
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if "=" in line and key in values:
            if key not in seen:
                result.append(f"{key}={values[key]}")
                seen.add(key)
        else:
            result.append(line)
    result.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
    return result


def migrate_storage():
    from app.config import LOCAL_DATA

    services = LOCAL_DATA / "services"
    services.mkdir(parents=True, exist_ok=True)
    with acquire_lock(services / "supervisor.lock"):
        ensure_free_ports()
        _migrate_storage()


def owns_command(name, command):
    normalized = command.replace("\\", "/").casefold()
    expected = {
        "api": "app.main:app",
        "worker": "app.worker",
        "ui": "frontend/node_modules/vite/bin/vite.js",
    }
    if name not in expected or ROOT.as_posix().casefold() + "/" not in normalized:
        return False
    return bool(re.search(r'(?:^|[/"\s])' + re.escape(expected[name]) + r'(?:["\s]|$)', normalized))


def stop_app():
    from app.config import LOCAL_DATA

    runtime = LOCAL_DATA / "services"
    runtime.mkdir(parents=True, exist_ok=True)
    try:
        lock = acquire_lock(runtime / "supervisor.lock")
    except RuntimeError:
        (runtime / "stop.request").touch()
        print("Stop requested; the supervisor will stop its own processes.")
        return
    with lock:
        state_file = runtime / "services.json"
        if not state_file.exists():
            print("No supervised application is running.")
            return
        state = json.loads(state_file.read_text())
        if state.get("root") != str(ROOT) or os.name != "nt":
            raise RuntimeError(
                "No live supervisor. Verify the old service processes manually before restarting."
            )
        for name in ("ui", "worker", "api"):
            pid = state.get(name)
            if not isinstance(pid, int) or pid <= 0:
                continue
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | Select-Object -ExpandProperty CommandLine",
                ],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if not result.stdout.strip():
                continue
            if not owns_command(name, result.stdout):
                raise RuntimeError(
                    f"Refusing to stop PID {pid}: it no longer belongs to this application."
                )
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        print("Verified orphaned application processes stopped.")


def _migrate_storage():
    from app.config import BACKEND_DIR, LOCAL_DATA
    from app.storage import backup, restore

    destination = LOCAL_DATA / "runtime"
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError("Local runtime already exists; refusing to overwrite meeting history")
    archive = LOCAL_DATA / "backups" / f"before-relocation-{time.time_ns()}.zip"
    backup(archive)
    restore(archive, destination)
    env = BACKEND_DIR / ".env"
    lines = env.read_text(encoding="utf-8-sig").splitlines()
    values = {
        "DATABASE_URL": f"sqlite:///{(destination / 'meeting_intelligence.db').as_posix()}",
        "STORAGE_DIR": (destination / "audio").as_posix(),
    }
    lines = update_environment(lines, values)
    temporary = env.with_suffix(".relocating")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, env)
    print(
        f"Verified database and audio moved to {destination}. Original files and backup retained."
    )


def start():
    from app.config import LOCAL_DATA, get_settings
    from app.database import initialize_database

    runtime = LOCAL_DATA / "services"
    runtime.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(runtime / "supervisor.lock")
    stop = runtime / "stop.request"
    stop.unlink(missing_ok=True)
    processes = []
    logs = []
    stopping = False

    def shutdown(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        ensure_free_ports()
        settings = get_settings()
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        initialize_database()
        node = shutil.which("node") or str(
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        )
        if not Path(node).is_file():
            raise RuntimeError(
                "Node.js is required. Install Node.js and frontend dependencies first."
            )
        commands = [
            (
                "api",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ],
                ROOT / "backend",
            ),
            ("worker", [sys.executable, "-m", "app.worker"], ROOT / "backend"),
            (
                "ui",
                [
                    node,
                    str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "5173",
                    "--strictPort",
                ],
                ROOT / "frontend",
            ),
        ]
        for name, command, cwd in commands:
            log = (runtime / f"{name}.log").open("a", encoding="utf-8")
            logs.append(log)
            child = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            processes.append((name, child))
        (runtime / "services.json").write_text(
            json.dumps(
                {
                    "supervisor": os.getpid(),
                    "root": str(ROOT),
                    **{name: child.pid for name, child in processes},
                }
            )
        )
        print(
            "Meeting Intelligence: http://localhost:5173 — API and worker supervised. Ctrl+C to stop.",
            flush=True,
        )
        while not stopping and not stop.exists():
            for name, child in processes:
                if child.poll() is not None:
                    raise RuntimeError(f"{name} exited. See {runtime / (name + '.log')}")
            time.sleep(0.5)
    finally:
        for _, child in reversed(processes):
            if child.poll() is None:
                # Windows venv Python has a launcher child: terminate this owned process tree.
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    child.terminate()
                child.wait(timeout=15)
        for log in logs:
            log.close()
        lock.close()
        stop.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["start", "stop", "migrate-storage"],
        default="start",
        nargs="?",
    )
    args = parser.parse_args()
    try:
        if args.command == "migrate-storage":
            migrate_storage()
        elif args.command == "stop":
            stop_app()
        else:
            start()
    except RuntimeError as error:
        sys.exit(str(error))
