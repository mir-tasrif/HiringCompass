"""F11 document-integrity assessment with compact, checkpointed evidence."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import PermanentError
from app.db.models import CandidateProfile, Document
from app.graphs.orchestrator import GraphRegistry, GraphSpec
from app.schemas.contracts import WorkKind
from app.services.cv_pipeline import _safe_path
from app.services.f11_integrity import inspect_integrity, integrity_requires_review


class IntegrityState(TypedDict, total=False):
    application_id: str
    signals: list[dict[str, Any]]
    review_required: bool
    policy_version: str


def build_integrity_graph(session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                          checkpointer: Any):
    async def inspect(state: IntegrityState) -> IntegrityState:
        application_id = uuid.UUID(state["application_id"])
        async with session_factory() as session:
            profile = await session.scalar(select(CandidateProfile).where(
                CandidateProfile.application_id == application_id))
            document = await session.scalar(select(Document).where(
                Document.application_id == application_id).order_by(Document.version.desc()))
            if profile is None or document is None:
                raise PermanentError("Extracted CV text and its document are required for integrity checks.")
            path = _safe_path(settings.file_storage_dir, document.file_path)
            signals = await asyncio.to_thread(inspect_integrity, path, profile.extracted_text, settings)
            return {"signals": signals, "policy_version": settings.integrity_policy_version}

    def route(state: IntegrityState) -> IntegrityState:
        return {"review_required": integrity_requires_review(state["signals"])}

    graph = StateGraph(IntegrityState)
    graph.add_node("inspect_document", inspect)
    graph.add_node("decide_review", route)
    graph.add_edge(START, "inspect_document")
    graph.add_edge("inspect_document", "decide_review")
    graph.add_edge("decide_review", END)
    return graph.compile(checkpointer=checkpointer)


def register_integrity_graph(registry: GraphRegistry, session_factory: async_sessionmaker[AsyncSession],
                             settings: Settings) -> None:
    registry.register(GraphSpec(WorkKind.INTEGRITY_CHECK,
        lambda checkpointer: build_integrity_graph(session_factory, settings, checkpointer)))
