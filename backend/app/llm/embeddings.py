"""Embedding function factory: batching and dimension validation on top of the Ollama client."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.config import Settings, get_settings
from app.core.errors import PermanentError
from app.llm.client import LLMClient

# Signature shared by the real embedder and test fakes.
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


# Build an EmbedFn that sends texts in batches and checks every vector has the expected size.
def make_embed_fn(client: LLMClient, settings: Settings | None = None, batch_size: int = 32) -> EmbedFn:
    settings = settings or get_settings()

    # Embed all texts, preserving order.
    async def embed(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(await client.embed(texts[start : start + batch_size]))
        if len(vectors) != len(texts) or any(len(v) != settings.embedding_dim for v in vectors):
            raise PermanentError(f"Embedding model must return {settings.embedding_dim}-dimensional vectors.")
        return vectors

    return embed
