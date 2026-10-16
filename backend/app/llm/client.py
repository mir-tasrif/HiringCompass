"""Async LLM client: OpenAI-compatible chat (local Ollama or any remote provider) plus local Ollama embeddings."""

from __future__ import annotations

import asyncio
import json
import re
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.prompts.common import JSON_SCHEMA_INSTRUCTION, JSON_SCHEMA_RETRY_INSTRUCTION
from app.core.errors import PermanentError, TransientError
from app.core.logging import get_logger

logger = get_logger("llm.client")


def _duration_seconds(value: str) -> float | None:
    """Parse provider reset durations such as 7.66s or 2m59.56s."""
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)", value.lower())
    if not parts:
        return None
    multipliers = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return sum(float(number) * multipliers[unit] for number, unit in parts)


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
        # Remote providers commonly enforce a shared request/token window; queue chat calls locally.
        self._chat_slots = asyncio.Semaphore(1 if s.llm_profile == "remote" else s.llm_max_concurrency)
        self._chat_cooldown_until = 0.0
        self._chat_tokens_remaining: int | None = None
        self._chat_tokens_reset_at = 0.0

    # Close both HTTP connection pools.
    async def aclose(self) -> None:
        await self._chat_http.aclose()
        await self._embed_http.aclose()

    # Send a request under the concurrency limit and translate HTTP/network failures.
    async def _request(self, http: httpx.AsyncClient, method: str, path: str,
                       payload: dict[str, Any] | None = None, *, service: str = "AI service") -> dict[str, Any]:
        try:
            async with self._slots:
                if http is self._chat_http:
                    async with self._chat_slots:
                        wait = self._chat_cooldown_until - time.monotonic()
                        if wait > 0:
                            await asyncio.sleep(wait)
                        # Groq reports remaining tokens and reset time after each call.
                        # Pace the next call before it triggers another 429.
                        if self._chat_tokens_remaining is not None:
                            if time.monotonic() >= self._chat_tokens_reset_at:
                                self._chat_tokens_remaining = None
                            elif payload is not None:
                                estimate = len(json.dumps(payload, ensure_ascii=False)) // 3 + 800
                                if estimate > self._chat_tokens_remaining:
                                    await asyncio.sleep(self._chat_tokens_reset_at - time.monotonic())
                                    self._chat_tokens_remaining = None
                        response = await http.request(method, path, json=payload)
                        remaining = response.headers.get("x-ratelimit-remaining-tokens")
                        reset = _duration_seconds(response.headers.get("x-ratelimit-reset-tokens", ""))
                        if remaining is not None and reset is not None:
                            try:
                                self._chat_tokens_remaining = max(0, int(float(remaining)))
                                self._chat_tokens_reset_at = time.monotonic() + reset
                            except ValueError:
                                pass
                        if response.status_code == 429:
                            self._chat_cooldown_until = max(self._chat_cooldown_until, time.monotonic() + 60)
                else:
                    response = await http.request(method, path, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(f"The {service} is unreachable or timed out.") from exc
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = response.headers.get("Retry-After")
            retry_seconds: float | None = None
            if retry_after:
                try:
                    retry_seconds = max(0.0, float(retry_after))
                except ValueError:
                    try:
                        retry_seconds = max(0.0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        pass
            if response.status_code == 429:
                if retry_seconds is None:
                    try:
                        body = response.json()
                        detail = str(body.get("error", body)) if isinstance(body, dict) else str(body)
                        match = re.search(r"(?:try again|retry)\s+in\s+(\d+(?:\.\d+)?)\s*(ms|s|sec|seconds?|m|min|minutes?)\b", detail, re.I)
                        if match:
                            value = float(match.group(1))
                            unit = match.group(2).lower()
                            retry_seconds = value / 1000 if unit == "ms" else value * 60 if unit.startswith("m") else value
                    except (ValueError, TypeError):
                        pass
                cooldown_seconds = min(self._settings.worker_backoff_max_seconds, max(60.0, retry_seconds or 0.0))
                if http is self._chat_http:
                    self._chat_cooldown_until = max(self._chat_cooldown_until, time.monotonic() + cooldown_seconds)
            raise TransientError(f"The {service} is busy (HTTP {response.status_code}).",
                                 retry_after_seconds=retry_seconds if retry_seconds is not None else (cooldown_seconds if response.status_code == 429 else None))
        if response.status_code in (401, 403):
            raise PermanentError("The AI service rejected the API key.")
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                error = body.get("error", body) if isinstance(body, dict) else {}
                detail = error.get("message", "") if isinstance(error, dict) else str(error)
            except (ValueError, TypeError):
                pass
            detail = re.sub(r"\s+", " ", str(detail)).strip()[:400]
            suffix = f": {detail}" if detail else "."
            raise PermanentError(f"The {service} rejected the request (HTTP {response.status_code}){suffix}")
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
                instruction = JSON_SCHEMA_INSTRUCTION + json.dumps(schema)
                payload["messages"] = [{"role": "system", "content": instruction}, *messages]
                payload["response_format"] = {"type": "json_object"}
        try:
            data = await self._request(self._chat_http, "POST", "/chat/completions", payload,
                                       service="configured chat AI service")
        except PermanentError as exc:
            # Some OpenAI-compatible providers reject constrained generation for a model even
            # though they accept ordinary chat. Retry once with an explicit JSON instruction.
            if schema is None or "HTTP 400" not in str(exc) or not any(
                marker in str(exc).lower() for marker in ("failed to generate json", "failed_generation")
            ):
                raise
            fallback = {**payload, "messages": [
                {"role": "system", "content": JSON_SCHEMA_RETRY_INSTRUCTION + json.dumps(schema)},
                *messages,
            ]}
            fallback.pop("response_format", None)
            data = await self._request(self._chat_http, "POST", "/chat/completions", fallback,
                                       service="configured chat AI service")
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        if not content:
            raise TransientError("The AI service returned an empty reply.")
        return content

    # Embed a batch of texts with the local Ollama embedding model.
    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = await self._request(self._embed_http, "POST", "/api/embed",
                                   {"model": self._settings.embedding_model, "input": texts},
                                   service="local Ollama embedding service")
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
