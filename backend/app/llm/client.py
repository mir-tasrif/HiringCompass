"""Async LLM client: OpenAI-compatible chat (local Ollama or any remote provider) plus local Ollama embeddings."""

from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import PermanentError, TransientError
from app.core.logging import get_logger

logger = get_logger("llm.client")


# Chat goes to LLM_BASE_URL (/chat/completions); embeddings always go to the local Ollama server.
# Failures map to Transient (retry) or Permanent (do not retry). Switch providers with LLM_PROFILE only.
class LLMClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings or get_settings()
        s = self._settings
        headers = {"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {}
        self._chat_http = httpx.AsyncClient(base_url=s.llm_base_url, headers=headers, timeout=s.llm_timeout_seconds, transport=transport)
        self._embed_http = httpx.AsyncClient(base_url=s.embedding_base_url, timeout=s.llm_timeout_seconds, transport=transport)
        self._slots = asyncio.Semaphore(s.llm_max_concurrency)

    # Close both HTTP connection pools.
    async def aclose(self) -> None:
        await self._chat_http.aclose()
        await self._embed_http.aclose()

    # Send a request under the concurrency limit and translate HTTP/network failures.
    async def _request(self, http: httpx.AsyncClient, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._slots:
                response = await http.request(method, path, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError("The AI service is unreachable or timed out.") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientError(f"The AI service is busy (HTTP {response.status_code}).")
        if response.status_code in (401, 403):
            raise PermanentError("The AI service rejected the API key.")
        if response.status_code >= 400:
            raise PermanentError(f"The AI service rejected the request (HTTP {response.status_code}).")
        return response.json()

    # Chat completion; `schema` (a JSON schema) constrains the reply to JSON of that shape.
    async def chat(self, messages: list[dict[str, str]], *, schema: dict[str, Any] | None = None,
                   temperature: float | None = None, seed: int | None = None) -> str:
        s = self._settings
        payload: dict[str, Any] = {
            "model": s.llm_model,
            "messages": list(messages),
            "stream": False,
            "temperature": s.llm_temperature if temperature is None else temperature,
            "seed": s.llm_seed if seed is None else seed,
        }
        if schema is not None:
            if s.llm_json_mode == "json_schema":
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "response", "schema": schema}}
            else:
                instruction = "Reply only with one JSON object matching this JSON schema: " + json.dumps(schema)
                payload["messages"] = [{"role": "system", "content": instruction}, *messages]
                payload["response_format"] = {"type": "json_object"}
        data = await self._request(self._chat_http, "POST", "/chat/completions", payload)
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        if not content:
            raise TransientError("The AI service returned an empty reply.")
        return content

    # Embed a batch of texts with the local Ollama embedding model.
    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = await self._request(self._embed_http, "POST", "/api/embed", {"model": self._settings.embedding_model, "input": texts})
        return data["embeddings"]

    # Report the active profile and whether the chat and embedding models are available.
    # `llm_model` is None when the provider does not expose a model list (cannot be verified).
    async def health(self) -> dict[str, Any]:
        s = self._settings
        try:
            listing = await self._request(self._chat_http, "GET", "/models")
            llm_ok: bool | None = _has_model({m["id"] for m in listing.get("data", [])}, s.llm_model)
        except PermanentError:
            llm_ok = None
        tags = await self._request(self._embed_http, "GET", "/api/tags")
        embedding_ok = _has_model({m["name"] for m in tags.get("models", [])}, s.embedding_model)
        return {"profile": s.llm_profile, "llm_model": llm_ok, "embedding_model": embedding_ok}


# True when `wanted` is installed; an untagged name (e.g. nomic-embed-text) matches any tag such as ":latest".
def _has_model(installed: set[str], wanted: str) -> bool:
    return wanted in installed or (":" not in wanted and any(n.startswith(f"{wanted}:") for n in installed))


# Process-wide client (one connection pool per process).
@lru_cache
def get_llm_client() -> LLMClient:
    return LLMClient()