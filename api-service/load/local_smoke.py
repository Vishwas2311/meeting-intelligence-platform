"""Isolated short 100-client regression on SQLite, real HTTP and a separate worker."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import AsyncExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def timing_summary(values):
    ordered = sorted(values)
    return {
        "samples": len(values),
        "mean_ms": round(statistics.mean(values), 3),
        "p95_ms": round(ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)], 3),
        "max_ms": round(ordered[-1], 3),
    }


async def workload(base, output_dir):
    timings = []
    operations = {name: [] for name in ["read", "poll", "upload", "queue", "write"]}
    errors = []
    ledger = []
    endpoint_times = {}
    transport_times = {}
    # Independent clients avoid one HTTPcore pool repeatedly scanning 100 idle sockets.
    context = ssl.create_default_context()
    async with AsyncExitStack() as stack:
        clients = [
            await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=base,
                    timeout=60,
                    verify=context,
                    trust_env=False,
                    limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
                )
            )
            for _ in range(100)
        ]
        client = clients[0]

        async def session(index):
            client = clients[index]
            headers = {"Authorization": f"Bearer synthetic-load-token-{index:03d}-" + "x" * 32}

            async def call(path, method="GET", body=None, files=None, data=None):
                began = time.perf_counter()
                stages = {}

                async def trace(event, info):
                    stage, _, state = event.rpartition(".")
                    if state == "started":
                        stages[stage] = time.perf_counter()
                    elif state in {"complete", "failed"} and stage in stages:
                        transport_times.setdefault(stage, []).append(
                            (time.perf_counter() - stages.pop(stage)) * 1000
                        )

                try:
                    response = await client.request(
                        method,
                        path,
                        headers=headers,
                        json=body,
                        files=files,
                        data=data,
                        extensions={"trace": trace},
                    )
                except httpx.HTTPError as error:
                    errors.append(
                        {"session": index, "error": type(error).__name__, "operation": method}
                    )
                    raise
                elapsed = (time.perf_counter() - began) * 1000
                timings.append(elapsed)
                endpoint = method + " " + re.sub(r"[0-9a-f-]{36}", "{id}", path.split("?", 1)[0])
                endpoint_times.setdefault(endpoint, []).append(elapsed)
                operation = (
                    "upload"
                    if files
                    else "queue"
                    if path.endswith("/mom/generate")
                    else "poll"
                    if "/jobs/" in path
                    else "read"
                    if method == "GET"
                    else "write"
                )
                operations[operation].append(elapsed)
                if response.status_code >= 400:
                    errors.append(
                        {"session": index, "status": response.status_code, "operation": method}
                    )
                    raise RuntimeError("Unexpected local-load response")
                return response.json()

            # A short ramp prevents initialization bursts from standing in for steady traffic.
            await asyncio.sleep(index / 100)
            created = await call(
                "/api/meetings",
                "POST",
                {"title": f"Synthetic session {index}", "consent_confirmed": True},
            )
            mid = created["id"]
            item = {"id": mid, "actor": f"client:load{index:03d}", "events": [], "audio": []}
            ledger.append(item)
            role = index % 10
            if role < 6:
                for _ in range(3):
                    history = await call("/api/meetings?limit=50")
                    assert all(m["id"] == mid for m in history)
                    await asyncio.sleep(0.5)
            else:
                await call(f"/api/meetings/{mid}/start", "POST")
                for sequence in range(2 if role < 8 else 1):
                    event = f"event-{sequence}"
                    await call(
                        f"/api/meetings/{mid}/segments",
                        "POST",
                        {
                            "segments": [
                                {
                                    "client_event_id": event,
                                    "text": "Synthetic discussion for regression only.",
                                    "start_ms": sequence * 5000,
                                    "end_ms": (sequence + 1) * 5000,
                                }
                            ]
                        },
                    )
                    item["events"].append(event)
                    if role < 8:
                        payload = f"Synthetic audio {index}-{sequence}".encode()
                        digest = hashlib.sha256(payload).hexdigest()
                        await call(
                            f"/api/meetings/{mid}/audio-chunks",
                            "POST",
                            files={"audio": ("part.webm", payload, "audio/webm")},
                            data={
                                "sequence": str(sequence),
                                "start_ms": str(sequence * 5000),
                                "end_ms": str((sequence + 1) * 5000),
                                "sha256": digest,
                            },
                        )
                        item["audio"].append(digest)
                        await asyncio.sleep(0.2)
                if role < 8:
                    await call(
                        f"/api/meetings/{mid}/manifest",
                        "POST",
                        {
                            "expected_chunks": 2,
                            "recorded_through_ms": 10000,
                            "finalized": True,
                            "pauses": [],
                        },
                    )
                await call(f"/api/meetings/{mid}/stop", "POST")
                if role >= 8:
                    queued = await call(f"/api/meetings/{mid}/mom/generate", "POST")
                    for _ in range(180):
                        job = await call(f"/api/meetings/{mid}/jobs/{queued['job_id']}")
                        if job["status"] == "completed":
                            break
                        assert job["status"] != "failed"
                        await asyncio.sleep(0.5)
                    else:
                        raise RuntimeError("Synthetic queue did not drain")
                    await call(f"/api/meetings/{mid}/mom")
            transcript = await call(f"/api/meetings/{mid}/transcript")
            assert set(item["events"]) == {s["client_event_id"] for s in transcript["segments"]}

        # Let every client settle before closing its shared transport or stopping the API.
        outcomes = await asyncio.gather(*(session(i) for i in range(100)), return_exceptions=True)
        if errors or any(isinstance(result, BaseException) for result in outcomes):
            output = output_dir / "local-load-100-failure.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"status": "FAIL", "errors": errors}, indent=2) + "\n")
            raise RuntimeError(
                "Local load failed; inspect the synthetic failure ledger and API log"
            )
        peer = ledger[0]
        headers = {"Authorization": "Bearer synthetic-load-token-999-" + "x" * 32}
        denied = await client.get(f"/api/meetings/{peer['id']}", headers=headers)
        assert denied.status_code == 403
        metrics = await client.get("/api/metrics", headers=headers)
        assert metrics.status_code == 200
        (output_dir / "load-metrics.txt").write_text(metrics.text, encoding="utf-8")
    return timings, errors, ledger, operations, endpoint_times, transport_times


def run(output_dir=None, sqlite_journal="delete"):
    from sqlalchemy import create_engine, text

    from scripts.smoke_http import SyntheticProvider

    output_dir = output_dir or ROOT / "output/implementation"
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="meeting-load-") as temporary:
        folder = Path(temporary)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        provider = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticProvider)
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        keys = {
            f"load{i:03d}": f"synthetic-load-token-{i:03d}-" + "x" * 32 for i in [*range(100), 999]
        }
        env = {
            **os.environ,
            "APP_ENV": "test",
            "AUTH_MODE": "api_key",
            "APP_API_TOKEN": "",
            "API_CLIENT_KEYS": json.dumps(keys),
            "API_ADMIN_CLIENTS": '["load999"]',
            "DATABASE_URL": f"sqlite:///{folder.as_posix()}/load.db",
            "STORAGE_DIR": str(folder / "audio"),
            "LLM_ENDPOINT": f"http://127.0.0.1:{provider.server_port}/chat",
            "LLM_MODEL": "synthetic",
            "LLM_API_KEY": "synthetic",
            "LLM_REQUEST_INTERVAL_SECONDS": "0",
            "AZURE_SPEECH_KEY": "",
            "AZURE_SPEECH_KEY_SECONDARY": "",
            "AZURE_SPEECH_REGION": "",
        }
        # Diagnostic only: use a disposable DB; never alter the developer's history/settings.
        probe_engine = create_engine(env["DATABASE_URL"])
        with probe_engine.connect() as connection:
            mode = connection.exec_driver_sql(f"PRAGMA journal_mode={sqlite_journal}").scalar()
            synchronous = connection.exec_driver_sql("PRAGMA synchronous").scalar()
            assert mode.lower() == sqlite_journal and synchronous == 2
        probe_engine.dispose()
        children = []
        api_log = (output_dir / "local-load-api.log").open("w", encoding="utf-8")
        worker_log = (output_dir / "local-load-worker.log").open("w", encoding="utf-8")
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--no-access-log",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=api_log,
                    stderr=api_log,
                    creationflags=flags,
                )
            )
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=base, timeout=1) as client:
                for _ in range(120):
                    try:
                        if client.get("/api/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Isolated load API did not start")
            children.append(
                subprocess.Popen(
                    [sys.executable, "-m", "app.worker"],
                    cwd=ROOT,
                    env=env,
                    stdout=worker_log,
                    stderr=worker_log,
                    creationflags=flags,
                )
            )
            began = time.perf_counter()
            timings, errors, ledger, operations, endpoints, transport = asyncio.run(
                workload(base, output_dir)
            )
            engine = create_engine(env["DATABASE_URL"])
            try:
                with engine.connect() as db:
                    assert db.scalar(text("SELECT COUNT(*) FROM meetings")) == 100
                    assert (
                        db.scalar(text("SELECT COUNT(*) FROM jobs WHERE status='completed'")) == 20
                    )
                    assert (
                        db.scalar(
                            text("SELECT COUNT(*) FROM jobs WHERE status IN ('pending','running')")
                        )
                        == 0
                    )
                    assert db.scalar(text("SELECT COUNT(*) FROM audio_chunks")) == 40
                    assert (
                        db.scalar(
                            text(
                                "SELECT COUNT(*) FROM mom_revisions WHERE source_job_id IS NOT NULL"
                            )
                        )
                        == 20
                    )
                    for item in ledger:
                        chunks = db.execute(
                            text(
                                "SELECT path,sha256 FROM audio_chunks WHERE meeting_id=:mid ORDER BY sequence"
                            ),
                            {"mid": item["id"]},
                        ).all()
                        assert [r.sha256 for r in chunks] == item["audio"]
                        for row in chunks:
                            assert (
                                hashlib.sha256(
                                    (folder / "audio" / row.path).read_bytes()
                                ).hexdigest()
                                == row.sha256
                            )
            finally:
                engine.dispose()
            ordered = sorted(timings)
            operation_stats = {
                name: {
                    "requests": len(values),
                    "p95_ms": round(sorted(values)[int(len(values) * 0.95)], 2),
                }
                for name, values in operations.items()
                if values
            }
            targets = {"read": 500, "poll": 500, "upload": 2000, "queue": 1000}
            result = {
                "mode": "short synthetic regression, not production qualification",
                "sessions": 100,
                "generator": "100 independent owner client pools; shared verified TLS context",
                "mix": "60 browsing/20 ingest/20 processing",
                "database": "isolated SQLite",
                "sqlite_journal": sqlite_journal,
                "sqlite_synchronous": "FULL",
                "provider": "local synthetic HTTP",
                "worker": "separate process",
                "requests": len(timings),
                "elapsed_seconds": round(time.perf_counter() - began, 2),
                "latency_ms": {
                    "median": round(statistics.median(timings), 2),
                    "p95": round(ordered[int(len(ordered) * 0.95)], 2),
                    "max": round(max(timings), 2),
                },
                "operation_latency": operation_stats,
                "candidate_latency_comparison": {
                    name: "PASS" if operation_stats[name]["p95_ms"] < target else "FAIL"
                    for name, target in targets.items()
                },
                "integrity_status": "PASS",
                "production_capacity_qualified": False,
                "unexpected_errors": len(errors),
                "completed_jobs": 20,
                "verified_audio_chunks": 40,
                "acknowledged_events_preserved": True,
                "cross_owner_denied": True,
                "client_endpoints": {
                    key: timing_summary(value) for key, value in endpoints.items()
                },
                "transport_stages": {
                    key: timing_summary(value) for key, value in transport.items()
                },
            }
            server_times = {}
            for line in (
                (output_dir / "local-load-api.log").read_text(encoding="utf-8").splitlines()
            ):
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("event") == "http.completed":
                    endpoint = record["method"] + " " + record["route"]
                    server_times.setdefault(endpoint, []).append(record["seconds"] * 1000)
            result["server_endpoints"] = {
                key: timing_summary(value) for key, value in server_times.items()
            }
            from prometheus_client.parser import text_string_to_metric_families

            samples = [
                sample
                for family in text_string_to_metric_families(
                    (output_dir / "load-metrics.txt").read_text()
                )
                for sample in family.samples
            ]
            measurements = {}
            for metric in [
                "meeting_admission_queue_duration_seconds",
                "meeting_admission_duration_seconds",
                "meeting_commit_duration_seconds",
                "meeting_sql_duration_seconds",
                "meeting_http_duration_seconds",
            ]:
                total = sum(s.value for s in samples if s.name == metric + "_sum")
                count = sum(s.value for s in samples if s.name == metric + "_count")
                measurements[metric] = {
                    "samples": int(count),
                    "mean_ms": round(total / count * 1000, 3) if count else None,
                }
            result["server_measurements"] = measurements
            result["sql_by_operation"] = {}
            for operation in {
                s.labels.get("operation")
                for s in samples
                if s.name == "meeting_sql_duration_seconds_count"
            }:
                total = sum(
                    s.value
                    for s in samples
                    if s.name == "meeting_sql_duration_seconds_sum"
                    and s.labels.get("operation") == operation
                )
                count = sum(
                    s.value
                    for s in samples
                    if s.name == "meeting_sql_duration_seconds_count"
                    and s.labels.get("operation") == operation
                )
                result["sql_by_operation"][operation] = {
                    "samples": int(count),
                    "mean_ms": round(total / count * 1000, 3) if count else None,
                }
            output = output_dir / "local-load-100.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result))
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
            api_log.close()
            worker_log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sqlite-journal", choices=["delete", "wal"], default="delete")
    arguments = parser.parse_args()
    run(arguments.output_dir, arguments.sqlite_journal)
