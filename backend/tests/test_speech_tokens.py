import asyncio

import pytest

from app.config import Settings
from app.speech_tokens import SpeechTokenCache


def test_concurrent_requests_share_one_token_and_report_remaining_lifetime(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.speech_tokens.time.monotonic", lambda: now[0])
    calls = []

    async def load(settings):
        calls.append(settings.azure_speech_region)
        await asyncio.sleep(0)
        return {
            "token": "synthetic",
            "region": settings.azure_speech_region,
            "expires_in_seconds": 600,
        }

    async def scenario():
        cache = SpeechTokenCache(load)
        settings = Settings(azure_speech_region="test", azure_speech_key="synthetic")
        values = await asyncio.gather(cache.get(settings), cache.get(settings))
        assert values[0] == values[1]
        assert len(calls) == 1
        now[0] += 120
        assert (await cache.get(settings))["expires_in_seconds"] == 480
        assert len(calls) == 1
        now[0] += 421
        await cache.get(settings)
        assert len(calls) == 2
        await cache.get(settings.model_copy(update={"azure_speech_key": "rotated"}))
        assert len(calls) == 3

    asyncio.run(scenario())


def test_failed_token_requests_are_not_cached():
    calls = []

    async def load(settings):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic outage")
        return {"token": "synthetic", "region": "test", "expires_in_seconds": 600}

    async def scenario():
        cache = SpeechTokenCache(load)
        settings = Settings()
        with pytest.raises(RuntimeError, match="synthetic outage"):
            await cache.get(settings)
        assert (await cache.get(settings))["token"] == "synthetic"
        assert len(calls) == 2

    asyncio.run(scenario())
