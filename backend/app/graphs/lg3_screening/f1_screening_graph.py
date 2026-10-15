"""Checkpointed F1 requirement assessment; database stage changes stay in the worker."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import PermanentError
from app.db.models import Application, CandidateProfile, Job, JobVersion
from app.graphs.orchestrator import GraphRegistry, GraphSpec
from app.prompts.user_messages import f1_screening_message
from app.prompts.system import CV_SCREENING
from app.rag.store import KnowledgeStore
from app.schemas.contracts import WorkKind


class RequirementResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    kind: Literal["mandatory", "preferred"]
    status: Literal["met", "partially_met", "unmet", "needs_review"]
    evidence: str
    explanation: str


class ScreeningOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suitability_score: int = Field(ge=0, le=100)
    explanation: str
    requirements: list[RequirementResult]


SCREENING_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "suitability_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "explanation": {"type": "string"},
        "requirements": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": ["mandatory", "preferred"]},
                "status": {"type": "string", "enum": ["met", "partially_met", "unmet", "needs_review"]},
                "evidence": {"type": "string"}, "explanation": {"type": "string"}},
            "required": ["text", "kind", "status", "evidence", "explanation"]}},
    }, "required": ["suitability_score", "explanation", "requirements"],
}


class ScreeningState(TypedDict, total=False):
    application_id: str
    suitability_score: int
    explanation: str
    requirements: list[dict[str, Any]]
    mandatory_pass: bool
    job_id: str
    job_version: int
    prompt_key: str
    prompt_version: str


def build_screening_graph(session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                          llm: Any, store: KnowledgeStore, checkpointer: Any):
    async def assess(state: ScreeningState) -> ScreeningState:
        async with session_factory() as session:
            application = await session.get(Application, uuid.UUID(state["application_id"]))
            if application is None:
                raise PermanentError("The CV application record no longer exists.")
            profile_row = await session.scalar(select(CandidateProfile).where(CandidateProfile.application_id == application.id))
            job = await session.get(Job, application.job_id)
            version = await session.scalar(select(JobVersion).where(JobVersion.job_id == application.job_id,
                JobVersion.version == application.job_version))
            if profile_row is None or not profile_row.profile or job is None or version is None:
                raise PermanentError("This CV needs a completed parsed profile and an active job description before ranking.")
            requirements = version.profile.get("requirements", [])
            job_context = {"title": job.title, "summary": version.profile.get("summary", ""),
                           "requirements": requirements, "about_company": version.profile.get("about_company", "")}
            query = f"{job.title} {job_context['summary']} " + " ".join(
                r.get("text", "") for r in requirements if isinstance(r, dict))
            hits = await store.search(session, namespace=settings.rag_namespace_company, query=query[:5000], top_k=4)
            company_context = [{"title": hit.title, "content": hit.content[:2500]} for hit in hits]
            prompt = f1_screening_message(job_context, profile_row.profile, profile_row.extracted_text, company_context)
            messages = [{"role": "system", "content": CV_SCREENING.text}, {"role": "user", "content": prompt}]
            expected = {(r.get("text", ""), r.get("kind", "preferred")) for r in requirements if isinstance(r, dict)}
            for attempt in range(2):
                raw = await llm.chat(messages, schema=SCREENING_SCHEMA)
                try:
                    result = ScreeningOutput.model_validate(json.loads(raw))
                    requirement_results = [requirement.model_dump() for requirement in result.requirements]
                    actual = {(r["text"], r["kind"]) for r in requirement_results}
                    if expected and not expected.issubset(actual):
                        raise ValueError("The model omitted one or more job requirements")
                    break
                except (TypeError, ValueError, KeyError, AttributeError, json.JSONDecodeError, ValidationError) as exc:
                    if attempt:
                        raise PermanentError("The AI returned incomplete requirement screening results after a repair attempt.") from exc
                    messages += [{"role": "assistant", "content": raw},
                        {"role": "user", "content": "Repair the screening JSON: include every job requirement exactly once with valid status, evidence, score, and explanation. Return only valid JSON."}]
            return {"suitability_score": result.suitability_score, "requirements": requirement_results,
                    "explanation": result.explanation[:8000], "job_id": str(job.id), "job_version": version.version,
                    "prompt_key": CV_SCREENING.key, "prompt_version": CV_SCREENING.version}

    def decide(state: ScreeningState) -> ScreeningState:
        return {"mandatory_pass": all(r["status"] in {"met", "partially_met"}
                for r in state["requirements"] if r["kind"] == "mandatory")}

    graph = StateGraph(ScreeningState)
    graph.add_node("assess_requirements", assess)
    graph.add_node("decide_mandatory", decide)
    graph.add_edge(START, "assess_requirements")
    graph.add_edge("assess_requirements", "decide_mandatory")
    graph.add_edge("decide_mandatory", END)
    return graph.compile(checkpointer=checkpointer)


def register_screening_graph(registry: GraphRegistry, session_factory: async_sessionmaker[AsyncSession],
                             settings: Settings, llm: Any, store: KnowledgeStore) -> None:
    registry.register(GraphSpec(WorkKind.APPLICATION_SCREENING,
        lambda checkpointer: build_screening_graph(session_factory, settings, llm, store, checkpointer)))
