"""Read-only check that the configured hosted LiveKit endpoint accepts our credentials."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from livekit import api

from app.core.config import get_settings


async def main() -> None:
    settings = get_settings()
    endpoint = urlsplit(settings.livekit_url)
    if endpoint.scheme != "wss" or not endpoint.hostname:
        raise RuntimeError("LIVEKIT_URL must be the secure LiveKit Cloud WebSocket URL.")
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise RuntimeError("LiveKit Cloud API key and secret are required.")
    api_url = settings.livekit_url.replace("wss://", "https://", 1)
    async with api.LiveKitAPI(url=api_url, api_key=settings.livekit_api_key,
                              api_secret=settings.livekit_api_secret) as client:
        await asyncio.wait_for(client.room.list_rooms(api.ListRoomsRequest()), timeout=15)
    print("LiveKit Cloud is reachable and credentials are valid.")


if __name__ == "__main__":
    asyncio.run(main())
