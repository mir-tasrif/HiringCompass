"""Short connection check for AssemblyAI streaming credentials and model access."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlencode

import websockets

from app.core.config import get_settings


async def main() -> None:
    settings = get_settings()
    if not settings.assemblyai_api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is missing.")
    url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode({
        "sample_rate": 16000, "speech_model": settings.assemblyai_streaming_model,
    })
    async with websockets.connect(url, additional_headers={"Authorization": settings.assemblyai_api_key},
                                  open_timeout=15) as stream:
        begin = json.loads(await asyncio.wait_for(stream.recv(), timeout=10))
        if begin.get("type") != "Begin":
            raise RuntimeError("AssemblyAI did not start a streaming session.")
        await stream.send(json.dumps({"type": "Terminate"}))
    print("AssemblyAI streaming is reachable and credentials are valid.")


if __name__ == "__main__":
    asyncio.run(main())
