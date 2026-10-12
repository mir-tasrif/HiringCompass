"""Start the durable CV extraction, integrity, and ranking worker."""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import SessionLocal
from app.graphs.common.checkpointer import open_checkpointer
from app.graphs.f2_scoring.graph import build_scoring_graph
from app.llm.client import get_llm_client
from app.llm.embeddings import make_embed_fn
from app.rag.store import KnowledgeStore
from app.services.cv_tasks import run_cv_worker


async def main() -> None:
    settings = get_settings()
    setup_logging("cv-worker", settings)
    llm = get_llm_client()
    store = KnowledgeStore(make_embed_fn(llm, settings), settings)
    async with open_checkpointer() as checkpointer:
        scoring_graph = build_scoring_graph(llm, checkpointer)
        await run_cv_worker(SessionLocal, settings, llm, store, scoring_graph)


if __name__ == "__main__":
    asyncio.run(main())
