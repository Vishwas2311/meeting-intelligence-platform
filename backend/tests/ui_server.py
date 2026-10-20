"""Isolated UI-test API with deterministic providers. Never run against the user's database."""

import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, HTTPException
from pydantic import BaseModel

if os.environ.get("MEETING_UI_TEST") != "1":
    raise RuntimeError("This fixture requires MEETING_UI_TEST=1 and a separate test database")

from app import main, recovery, worker
from app.config import BACKEND_DIR, get_settings
from app.database import SessionLocal, engine
from app.models import Meeting, Mom, Segment
from app.providers import ProviderError
from app.schemas import MomData
from app.security import require_actor

assert engine.url.database != str(BACKEND_DIR / "meeting_intelligence.db")
assert get_settings().app_api_token == "synthetic-ui-test-token"

mode = "success"
calls = 0


async def speech(settings):
    if mode == "speech-auth":
        raise HTTPException(503, "Synthetic Speech authentication rejected (HTTP 401)")
    return {"token": "synthetic-token", "region": "test", "expires_in_seconds": 600}


async def minutes(settings, title, transcript, ids, gaps):
    global calls
    calls += 1
    await asyncio.sleep(0.1)
    if mode == "llm-auth":
        raise ProviderError("Synthetic LLM authentication rejected (HTTP 401).")
    if mode == "rate-limit" and calls == 1:
        raise ProviderError("Synthetic rate limit", retryable=True, retry_after=0.2)
    return MomData(
        title=title,
        summary="The synthetic team agreed to review a pilot.",
        discussion_points=["The pilot requires review before release."],
        decisions=[{"text": "Review the pilot", "source_segment_ids": ids[:1]}],
        transcript_gaps=gaps,
    ).model_dump()


async def fast(settings, audio, filename, media_type, language):
    assert audio.startswith(b"RIFF")
    return {
        "phrases": [
            {
                "text": "Recovered synthetic pilot discussion.",
                "offsetMilliseconds": 0,
                "durationMilliseconds": 100,
            }
        ]
    }


recovery.fast_transcribe_file = fast
main.azure_speech_token = speech
worker.llm_mom = minutes
original_lifespan = main.app.router.lifespan_context


@asynccontextmanager
async def lifespan(app):
    async with original_lifespan(app):
        task = asyncio.create_task(worker.main())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = main.app
app.router.lifespan_context = lifespan


class Mode(BaseModel):
    value: str


@app.post("/__test__/mode", dependencies=[Depends(require_actor)])
def set_mode(body: Mode):
    global mode, calls
    mode, calls = body.value, 0
    return {"mode": mode}


@app.post("/__test__/seed", dependencies=[Depends(require_actor)])
def seed():
    with SessionLocal() as db:
        for i in range(12):
            meeting = Meeting(
                title=f"Synthetic history {i:02d}",
                status="ready_review",
                capture_status="stopped",
                stt_status="connected",
                sync_status="synced",
                consent_confirmed=True,
            )
            db.add(meeting)
            db.flush()
            db.add(
                Segment(
                    meeting_id=meeting.id,
                    client_event_id=f"seed-{i}",
                    text="Synthetic pilot discussion.",
                    start_ms=0,
                    end_ms=1000,
                )
            )
            db.add(
                Mom(
                    meeting_id=meeting.id,
                    data=MomData(
                        title=meeting.title, summary="Synthetic saved minutes."
                    ).model_dump(),
                )
            )
        db.commit()
    return {"seeded": 12}
