"""Start the durable CV extraction, integrity, and ranking worker."""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import SessionLocal
from app.llm.client import get_llm_client
from app.llm.embeddings import make_embed_fn
from app.rag.store import KnowledgeStore
from app.services.cv_tasks import run_cv_worker


async def main() -> None:
    settings = get_settings()
    setup_logging("cv-worker", settings)
    llm = get_llm_client()
    store = KnowledgeStore(make_embed_fn(llm, settings), settings)
    await run_cv_worker(SessionLocal, settings, llm, store)


if __name__ == "__main__":
    asyncio.run(main())
