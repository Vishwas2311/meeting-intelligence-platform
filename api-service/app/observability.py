"""Low-cardinality metrics and redacted operational events."""

import json
import logging
from contextvars import ContextVar

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

registry = CollectorRegistry()
admission_queue_latency = Histogram(
    "meeting_admission_queue_duration_seconds",
    "Wait for the bounded request-admission thread before database work",
    registry=registry,
)
admission_latency = Histogram(
    "meeting_admission_duration_seconds",
    "Shared request admission including pool/SQL/commit",
    registry=registry,
)
sql_latency = Histogram(
    "meeting_sql_duration_seconds",
    "Successful DB cursor execution; excludes pool checkout and commit",
    ["operation"],
    registry=registry,
)
commit_latency = Histogram(
    "meeting_commit_duration_seconds", "Successful session flush and commit", registry=registry
)
expired_buckets = Gauge(
    "meeting_expired_rate_buckets",
    "Expired admission rows awaiting bounded cleanup",
    registry=registry,
)
current_request = ContextVar("meeting_request_id", default=None)
requests = Counter(
    "meeting_http_requests_total",
    "Completed HTTP requests",
    ["route", "method", "status"],
    registry=registry,
)
latency = Histogram(
    "meeting_http_duration_seconds", "HTTP request duration", ["route"], registry=registry
)
queued = Gauge("meeting_jobs", "Durable jobs by status", ["status"], registry=registry)
oldest = Gauge(
    "meeting_oldest_runnable_job_seconds", "Age of oldest runnable job", registry=registry
)
workers = Gauge("meeting_live_workers", "Fresh non-draining worker heartbeats", registry=registry)
free_disk = Gauge(
    "meeting_storage_free_bytes", "Available persistent audio bytes", registry=registry
)
pool_used = Gauge(
    "meeting_pool_checked_out", "Connections checked out by this API process", registry=registry
)
log = logging.getLogger("meeting-events")
log.setLevel(logging.INFO)
if not log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
log.propagate = False
# HTTP libraries emit full private provider URLs at INFO under the worker's root logger.
for library in ("httpx", "httpcore"):
    logging.getLogger(library).setLevel(logging.WARNING)


def observe_request(scope, seconds, status=500):
    route = getattr(scope.get("route"), "path", "unmatched")
    method = (
        scope["method"]
        if scope["method"] in {"GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS", "HEAD"}
        else "OTHER"
    )
    requests.labels(route, method, str(status)).inc()
    latency.labels(route).observe(seconds)
    event(
        "http.completed",
        request_id=scope.get("state", {}).get("request_id"),
        outcome=str(status),
        route=route,
        method=scope["method"],
        seconds=round(seconds, 4),
    )
    if status in {401, 403}:
        principal = scope.get("state", {}).get("principal")
        event(
            "security.denied",
            request_id=scope.get("state", {}).get("request_id"),
            actor=getattr(principal, "actor", "unverified"),
            outcome=str(status),
            route=route,
            method=method,
        )


def event(action, **fields):
    # Fixed allowlist: callers cannot accidentally log content, URLs or credentials.
    safe = {
        key: value
        for key, value in fields.items()
        if key
        in {
            "request_id",
            "job_id",
            "worker_id",
            "error_type",
            "outcome",
            "actor",
            "count",
            "route",
            "method",
            "seconds",
        }
    }
    log.info(json.dumps({"event": action, **safe}, separators=(",", ":")))


def metrics():
    import shutil
    from datetime import timedelta

    from sqlalchemy import func, or_, select

    from .config import get_settings
    from .database import SessionLocal, engine
    from .models import Job, RateBucket, WorkerState, now

    with SessionLocal() as db:
        expired_buckets.set(
            db.scalar(
                select(func.count()).select_from(RateBucket).where(RateBucket.expires_at < now())
            )
        )
        values = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        for status in ["pending", "running", "completed", "failed"]:
            queued.labels(status).set(values.get(status, 0))
        at = db.scalar(
            select(func.min(Job.created_at)).where(
                Job.status == "pending", or_(Job.run_after.is_(None), Job.run_after <= now())
            )
        )
        oldest.set(max(0, (now() - at).total_seconds()) if at else 0)
        workers.set(
            db.scalar(
                select(func.count())
                .select_from(WorkerState)
                .where(
                    WorkerState.state == "running",
                    WorkerState.seen_at >= now() - timedelta(seconds=45),
                )
            )
        )
    free_disk.set(shutil.disk_usage(get_settings().storage_dir).free)
    if hasattr(engine.pool, "checkedout"):
        pool_used.set(engine.pool.checkedout())
    return generate_latest(registry)
