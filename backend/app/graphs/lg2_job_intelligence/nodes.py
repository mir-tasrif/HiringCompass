"""LG2 node functions (async, guarded). Each returns a partial state update."""

from __future__ import annotations

import uuid
from typing import Any, Callable

from langgraph.types import interrupt
from pydantic import ValidationError

from app.core.errors import PermanentError
from app.graphs.common.handoff import to_state
from app.graphs.common.node_guard import guarded_node
from app.graphs.lg2_job_intelligence import prompts
from app.graphs.lg2_job_intelligence.deps import JobGraphDeps
from app.graphs.lg2_job_intelligence.drafts import ExtractedCriteria, JobProfileDraft, RubricDraft
from app.graphs.lg2_job_intelligence.posting import build_posting_text
from app.llm.structured import generate_structured
from app.schemas.contracts import ApprovalGate, EvaluationRubric, JobProfile, Requirement, RubricDimension
from app.services import approvals, jobs
from app.services.fairness import find_protected_terms

_OPTIONS = ["approve", "edit", "reject"]


# Scale weights to sum to exactly 1 (4 decimals); the rounding residual goes to the largest weight.
def normalize_weights(dimensions: list[RubricDimension]) -> list[RubricDimension]:
    total = sum(d.weight for d in dimensions)
    if total <= 0:
        return dimensions
    scaled = [round(d.weight / total, 4) for d in dimensions]
    scaled[scaled.index(max(scaled))] = round(scaled[scaled.index(max(scaled))] + round(1 - sum(scaled), 4), 4)
    return [d.model_copy(update={"weight": w}) for d, w in zip(dimensions, scaled)]


# Describe validation errors by field/message only (no user text).
def _describe(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc']) or 'draft'}: {e['msg'].removeprefix('Value error, ')}" for e in exc.errors()]


# Build the LG2 nodes bound to their dependencies.
def make_nodes(deps: JobGraphDeps) -> dict[str, Callable]:
    namespace = deps.settings.rag_namespace_company

    # Check the job exists and decide the draft version number.
    @guarded_node("load_inputs")
    async def load_inputs(state: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.UUID(state["job_id"])
        async with deps.session_factory() as session:
            if await jobs.get_job(session, job_id) is None:
                raise PermanentError("Job not found.")
            version = state.get("draft_version") or await jobs.next_version(session, job_id)
        return {"draft_version": version, "retry_count": 0, "validation_errors": [], "status": "drafting"}

    # Turn the recruiter prompt into structured criteria and decide whether they are sufficient.
    @guarded_node("extract_criteria")
    async def extract_criteria(state: dict[str, Any]) -> dict[str, Any]:
        criteria = await generate_structured(deps.llm, ExtractedCriteria, system=prompts.EXTRACT_SYSTEM, user=state["recruiter_prompt"])
        sufficient = criteria.sufficient and bool(criteria.title.strip()) and len(criteria.requirements) >= 2
        missing = criteria.missing_info or ["the role title and at least two requirements"]
        question = None if sufficient else "I need a bit more detail before drafting: " + "; ".join(missing) + "."
        return {"criteria": criteria.model_dump(mode="json"), "criteria_sufficient": sufficient, "clarification_question": question}

    # Stop the run and hand the question back to the recruiter.
    @guarded_node("ask_clarification")
    async def ask_clarification(state: dict[str, Any]) -> dict[str, Any]:
        return {"status": "awaiting_recruiter"}

    # Retrieve relevant company hiring-policy passages (empty when no corpus is ingested).
    @guarded_node("retrieve_policy")
    async def retrieve_policy(state: dict[str, Any]) -> dict[str, Any]:
        criteria = ExtractedCriteria(**state["criteria"])
        query = criteria.title + ". " + "; ".join(r.text for r in criteria.requirements[:5])
        async with deps.session_factory() as session:
            hits = await deps.store.search(session, namespace=namespace, query=query, top_k=3)
        passages = [{"chunk_id": str(h.chunk_id), "title": h.title, "version": h.version, "content": h.content} for h in hits]
        return {"policy_passages": passages}

    # Draft the job profile (uses recruiter feedback and earlier validation errors when present).
    @guarded_node("generate_profile")
    async def generate_profile(state: dict[str, Any]) -> dict[str, Any]:
        user = prompts.profile_prompt(state["criteria"], state.get("policy_passages", []), state.get("feedback"), state.get("validation_errors", []))
        draft = await generate_structured(deps.llm, JobProfileDraft, system=prompts.PROFILE_SYSTEM, user=user)
        return {"job_profile": draft.model_dump(mode="json")}

    # Draft the rubric and normalise its weights to sum to 1.
    @guarded_node("generate_rubric")
    async def generate_rubric(state: dict[str, Any]) -> dict[str, Any]:
        user = prompts.rubric_prompt(state["job_profile"], state["criteria"].get("priorities", ""), state.get("validation_errors", []))
        draft = await generate_structured(deps.llm, RubricDraft, system=prompts.RUBRIC_SYSTEM, user=user)
        dims = normalize_weights(draft.dimensions)
        return {"rubric": {"dimensions": [d.model_dump(mode="json") for d in dims]}}

    # Assign ids, validate contracts, anchors, uniqueness and job-relevance; record errors for the retry.
    @guarded_node("validate_draft")
    async def validate_draft(state: dict[str, Any]) -> dict[str, Any]:
        job_id, version = uuid.UUID(state["job_id"]), state["draft_version"]
        draft, errors = state["job_profile"], []
        profile = rubric = None
        try:
            profile = JobProfile(
                job_id=job_id, version=version, title=draft["title"], summary=draft["summary"],
                responsibilities=draft.get("responsibilities", []),
                requirements=[Requirement(requirement_id=f"R{i}", **r) for i, r in enumerate(draft["requirements"], start=1)],
                policy_source_ids=[p["chunk_id"] for p in state.get("policy_passages", [])],
            )
            rubric = EvaluationRubric(
                rubric_id=uuid.uuid5(job_id, f"rubric-{version}"), job_id=job_id, job_version=version, version=1,
                dimensions=[RubricDimension(**d) for d in state["rubric"]["dimensions"]],
            )
        except ValidationError as exc:
            errors += _describe(exc)
        if profile:
            texts = [r.text.strip().lower() for r in profile.requirements]
            if len(set(texts)) != len(texts):
                errors.append("requirements: duplicate requirements found")
            checked = [profile.summary, *profile.responsibilities, *(r.text for r in profile.requirements)]
            if rubric:
                checked += [d.description for d in rubric.dimensions] + [a for d in rubric.dimensions for a in d.anchors.values()]
            for term in find_protected_terms(" ".join(checked)):
                errors.append(f"job-relevance: remove reference to the protected characteristic '{term}'")
        if rubric:
            errors += [f"rubric: dimension '{d.key}' needs at least two score anchors" for d in rubric.dimensions if len(d.anchors) < 2]
        if errors:
            return {"validation_errors": errors, "retry_count": state.get("retry_count", 0) + 1}
        return {"job_profile": to_state(profile), "rubric": to_state(rubric), "validation_errors": []}

    # End the run after repeated invalid drafts; the recruiter can retry with a clearer prompt.
    @guarded_node("fail_draft")
    async def fail_draft(state: dict[str, Any]) -> dict[str, Any]:
        reason = "; ".join(state.get("validation_errors", [])[:3])
        return {"error": f"The draft could not be validated: {reason}", "error_category": "draft_invalid", "status": "failed"}

    # Persist the validated draft version.
    @guarded_node("save_draft")
    async def save_draft(state: dict[str, Any]) -> dict[str, Any]:
        async with deps.session_factory() as session:
            await jobs.save_draft(session, uuid.UUID(state["job_id"]), state["draft_version"], state["job_profile"], state["rubric"])
        return {"status": "draft_saved"}

    # Human gate: persist an approval record, then pause until a decision arrives (survives restarts).
    @guarded_node("await_activation")
    async def await_activation(state: dict[str, Any]) -> dict[str, Any]:
        profile = state["job_profile"]
        mandatory = sum(r["kind"] == "mandatory" for r in profile["requirements"])
        summary = f"{profile['title']} v{state['draft_version']}: {len(profile['requirements'])} requirements ({mandatory} mandatory)"
        async with deps.session_factory() as session:
            approval = await approvals.request_approval(
                session, gate=ApprovalGate.JOB_ACTIVATION, version=state["draft_version"], evidence_summary=summary,
                allowed_options=_OPTIONS, job_id=uuid.UUID(state["job_id"]), run_id=state.get("run_id"),
            )
        answer = interrupt({"approval_id": str(approval.id), "gate": ApprovalGate.JOB_ACTIVATION.value, "version": state["draft_version"]})
        option = answer.get("option") if isinstance(answer, dict) else None
        if option not in _OPTIONS:
            raise PermanentError("Invalid activation decision.")
        return {"approval_id": str(approval.id), "decision": option, "feedback": answer.get("reason")}

    # Approved: make this version the active, immutable one.
    @guarded_node("activate_version")
    async def activate_version(state: dict[str, Any]) -> dict[str, Any]:
        async with deps.session_factory() as session:
            await jobs.activate_version(session, uuid.UUID(state["job_id"]), state["draft_version"])
        return {"active_version": state["draft_version"], "status": "active"}

    # Create copyable posting text for manual posting and store it with the version.
    @guarded_node("prepare_posting_text")
    async def prepare_posting_text(state: dict[str, Any]) -> dict[str, Any]:
        text = build_posting_text(state["job_profile"])
        async with deps.session_factory() as session:
            await jobs.set_posting_text(session, uuid.UUID(state["job_id"]), state["draft_version"], text)
        return {"posting_text": text}

    # Edit requested: retire this draft and start the next version with the recruiter's feedback.
    @guarded_node("prepare_edit")
    async def prepare_edit(state: dict[str, Any]) -> dict[str, Any]:
        async with deps.session_factory() as session:
            await jobs.set_status(session, uuid.UUID(state["job_id"]), state["draft_version"], "superseded")
        return {"draft_version": state["draft_version"] + 1, "retry_count": 0, "validation_errors": [], "decision": None, "status": "redrafting"}

    # Rejected: mark the draft discarded.
    @guarded_node("mark_discarded")
    async def mark_discarded(state: dict[str, Any]) -> dict[str, Any]:
        async with deps.session_factory() as session:
            await jobs.set_status(session, uuid.UUID(state["job_id"]), state["draft_version"], "discarded")
        return {"status": "discarded"}

    return {f.__name__: f for f in (load_inputs, extract_criteria, ask_clarification, retrieve_policy, generate_profile, generate_rubric,
                                     validate_draft, fail_draft, save_draft, await_activation, activate_version, prepare_posting_text,
                                     prepare_edit, mark_discarded)}