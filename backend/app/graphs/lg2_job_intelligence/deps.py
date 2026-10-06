"""Dependencies injected into the LG2 graph (swapped for fakes in tests)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.rag.store import KnowledgeStore


# Everything the job-drafting nodes need from the outside world.
@dataclass(frozen=True)
class JobGraphDeps:
    llm: Any  # LLMClient-compatible: async chat(messages, schema=...)
    store: KnowledgeStore
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    retry_interval: float = 1.0
    max_draft_attempts: int = 2