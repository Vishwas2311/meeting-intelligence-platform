"""Bound request streams and authenticate before multipart bodies are parsed."""

import re
import time
from uuid import uuid4

import anyio
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse


class BodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(413, "Request body too large")


class RequestGuard:
    def __init__(self, app):
        from .config import get_settings

        self.app = app
        settings = get_settings()
        # Admission waits asynchronously; it must not occupy all endpoint threads/connections.
        self.admission_limiter = anyio.CapacityLimiter(
            max(1, (settings.db_pool_size + settings.db_max_overflow) // 2)
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from .config import get_settings
        from .security import require_principal

        settings = get_settings()
        path, method = scope["path"], scope["method"]
        headers = {k.decode().lower(): v.decode("latin1") for k, v in scope["headers"]}
        request_id = headers.get("x-request-id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        from .observability import current_request

        correlation = current_request.set(request_id)
        limit = settings.max_json_bytes
        if path.endswith("/audio-chunks"):
            limit = (settings.max_audio_chunk_mb + 1) * 1024**2
        elif path.startswith("/api/recaps/") and path.endswith("/audio"):
            limit = (settings.max_recap_audio_mb + 1) * 1024**2
        total = 0
        started = False
        response_status = 500
        began = time.monotonic()

        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise BodyTooLarge
            return message

        async def observed_send(message):
            nonlocal started, response_status
            if message["type"] == "http.response.start":
                started = True
                response_status = message["status"]
                message["headers"].append((b"x-request-id", request_id.encode()))
                message["headers"].append((b"x-api-version", b"1"))
                if path.startswith("/api/"):
                    message["headers"] = [
                        (k, v) for k, v in message["headers"] if k.lower() != b"cache-control"
                    ]
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        try:
            if "content-length" in headers:
                try:
                    length = int(headers["content-length"])
                except ValueError:
                    raise HTTPException(400, "Invalid Content-Length") from None
                if length < 0:
                    raise HTTPException(400, "Invalid Content-Length")
                if length > limit:
                    raise BodyTooLarge
            public = path in {"/api/health", "/api/ready", "/api/processing-ready"}
            if path.startswith("/api/") and not public and method != "OPTIONS":
                value = headers.get("authorization", "")
                scheme, _, token = value.partition(" ")
                credential = (
                    HTTPAuthorizationCredentials(scheme=scheme, credentials=token)
                    if scheme.lower() == "bearer" and token
                    else None
                )
                principal = await anyio.to_thread.run_sync(
                    lambda: require_principal(credential, settings)
                )
                scope["state"]["principal"] = principal
                from .limits import admit_request
                from .observability import admission_latency, admission_queue_latency

                admission_began = time.monotonic()

                def admit():
                    admission_queue_latency.observe(time.monotonic() - admission_began)
                    admit_request(principal.actor, path, method, settings)

                try:
                    await anyio.to_thread.run_sync(admit, limiter=self.admission_limiter)
                finally:
                    admission_latency.observe(time.monotonic() - admission_began)
            await self.app(scope, bounded_receive, observed_send)
        except BodyTooLarge:
            if not started:
                await JSONResponse({"detail": "Request body too large"}, 413)(
                    scope, receive, observed_send
                )
            else:
                raise
        except HTTPException as exc:
            if not started:
                await JSONResponse({"detail": exc.detail}, exc.status_code, headers=exc.headers)(
                    scope, receive, observed_send
                )
            else:
                raise
        except SQLAlchemyError as exc:
            from .observability import event

            event("database.unavailable", request_id=request_id, error_type=type(exc).__name__)
            if not started:
                await JSONResponse(
                    {"detail": "Database temporarily unavailable"},
                    503,
                    headers={"Retry-After": "5"},
                )(scope, receive, observed_send)
            else:
                raise
        finally:
            from .observability import observe_request

            observe_request(scope, time.monotonic() - began, response_status)
            current_request.reset(correlation)
