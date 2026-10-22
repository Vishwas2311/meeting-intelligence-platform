from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.exceptions import ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from .config import get_settings
from .database import initialize_database
from .improvements import router as improvements_router
from .local_cache import router as local_cache_router
from .providers import azure_speech_token
from .recaps import router as recaps_router
from .request_limits import RequestGuard
from .routes import audio, client_events, meetings, minutes, speech, system, transcripts
from .security import require_actor
from .speech_tokens import SpeechTokenCache


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.app_env == "production":
        from .audio_store import check_volume

        check_volume(settings)
    else:
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
    initialize_database(runtime=True)
    app.state.speech_tokens = SpeechTokenCache(lambda settings: azure_speech_token(settings))
    yield


app = FastAPI(
    title=get_settings().app_name,
    version="1.0.0",
    description="Independent meeting API. Authenticate using a company-issued access token in JWT mode or a configured server-client key in API-key mode. Processing is asynchronous; poll meeting jobs before fetching results.",
    lifespan=lifespan,
)


@app.exception_handler(ResponseValidationError)
async def invalid_response(request, error):
    from .observability import event

    # FastAPI's default exception text embeds rejected output, including meeting content.
    event(
        "response.invalid",
        request_id=getattr(request.state, "request_id", None),
        error_type="ResponseValidationError",
        count=len(error.errors()),
    )
    return PlainTextResponse("Internal Server Error", status_code=500)


app.include_router(recaps_router)
app.include_router(improvements_router)
if get_settings().browser_recovery_enabled:
    app.include_router(local_cache_router)
app.include_router(client_events.router)
app.add_middleware(RequestGuard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "If-None-Match", "X-Request-ID"],
    expose_headers=["ETag", "X-Request-ID", "Retry-After", "X-API-Version"],
)


for router in (
    system.router,
    meetings.router,
    transcripts.router,
    audio.router,
    minutes.router,
    speech.router,
):
    app.include_router(router)


@app.get("/api/openapi.json", include_in_schema=False, dependencies=[Depends(require_actor)])
def protected_schema():
    """Integration schema through the API gateway; existing local docs stay compatible."""
    return app.openapi()


original_openapi = app.openapi


def published_openapi():
    from .response_models import document_errors

    if app.openapi_schema is None:
        app.openapi_schema = document_errors(original_openapi())
    return app.openapi_schema


app.openapi = published_openapi
