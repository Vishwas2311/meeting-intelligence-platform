import asyncio
import time

from .config import Settings


class SpeechTokenCache:
    """Share short-lived Azure credentials in memory, scoped to the configured resource."""

    def __init__(self, load):
        self.load = load
        self.lock = asyncio.Lock()
        self.resource = None
        self.value = None
        self.expires_at = 0.0

    async def get(self, settings: Settings):
        resource = (settings.azure_speech_region, settings.azure_speech_key)
        async with self.lock:
            started = time.monotonic()
            if self.resource != resource or self.expires_at - started <= 60:
                value = await self.load(settings)
                self.value = value
                self.resource = resource
                self.expires_at = started + value["expires_in_seconds"]
            return {
                **self.value,
                "expires_in_seconds": max(0, int(self.expires_at - time.monotonic())),
            }
