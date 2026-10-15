"""Start the CV worker and register checkpointed F11, F1, and F2 graphs."""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import SessionLocal
from app.graphs.common.checkpointer import open_checkpointer
from app.graphs.f2_scoring.f2_scoring_graph import register_scoring_graph
from app.graphs.lg3_screening.f11_integrity_graph import register_integrity_graph
from app.graphs.lg3_screening.f1_screening_graph import register_screening_graph
from app.graphs.orchestrator import GraphRegistry
from app.schemas.contracts import WorkKind
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
        graph_registry = GraphRegistry()
        register_scoring_graph(graph_registry, llm)
        register_screening_graph(graph_registry, SessionLocal, settings, llm, store)
        register_integrity_graph(graph_registry, SessionLocal, settings)
        scoring_graph = graph_registry.get(WorkKind.SCORING, checkpointer)
        screening_graph = graph_registry.get(WorkKind.APPLICATION_SCREENING, checkpointer)
        integrity_graph = graph_registry.get(WorkKind.INTEGRITY_CHECK, checkpointer)
        await run_cv_worker(SessionLocal, settings, llm, store, scoring_graph, screening_graph, integrity_graph)


if __name__ == "__main__":
    asyncio.run(main())
