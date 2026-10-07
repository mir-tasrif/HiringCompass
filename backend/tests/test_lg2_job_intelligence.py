"""LG2 job intelligence graph tests: real Postgres + checkpointer, scripted LLM, fake embedder."""

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import psycopg
import pytest
import pymupdf
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.graphs.orchestrator as orch
from app.core.config import Settings
from app.core.errors import ApprovalError, TransientError
from app.db.models import Approval, Job, KnowledgeSource, User
from app.graphs.common.checkpointer import open_checkpointer
from app.graphs.lg2_job_intelligence.deps import JobGraphDeps
from app.graphs.lg2_job_intelligence.graph import register_job_graph
from app.graphs.lg2_job_intelligence.nodes import normalize_weights
from app.graphs.orchestrator import GraphRegistry, resume_graph, run_graph, run_status
from app.rag.store import KnowledgeStore
from app.rag.ingest import ingest_directory
from app.schemas.contracts import ApprovalGate, RubricDimension, WorkKind
from app.services import approvals, jobs
from tests.helpers import ScriptedLLM, fake_embed

DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://hc_user:change_me@localhost:5432/hiringcompass")
CHK_URL = os.getenv("CHECKPOINT_DB_URL", "postgresql://hc_user:change_me@localhost:5432/hiringcompass")

REQS = [
    {"text": "3+ years of Python web development", "kind": "mandatory", "weight": 3, "min_years": 3, "equivalents": []},
    {"text": "Experience with PostgreSQL", "kind": "mandatory", "weight": 2},
    {"text": "Docker experience", "kind": "preferred", "weight": 1},
]
CRITERIA = {"title": "Backend Python Engineer", "summary": "Builds APIs", "responsibilities": ["Build REST APIs"],
            "requirements": REQS, "priorities": "skills matter most", "experience": "3+ years", "education": "B.Sc in Computer Science",
            "missing_info": [], "sufficient": True}
PROFILE = {"title": "Backend Python Engineer", "summary": "Builds and runs our APIs.", "responsibilities": ["Build REST APIs"], "requirements": REQS}
ANCHORS = {"0": "no evidence", "50": "partial evidence", "100": "strong evidence"}


# Rubric JSON with the given (key, weight) pairs.
def rubric_json(pairs):
    return json.dumps({"dimensions": [{"key": k, "description": f"{k} fit", "weight": w, "anchors": ANCHORS} for k, w in pairs]})


FULL = [("technical_skills", 0.5), ("relevant_experience", 0.3), ("qualifications", 0.1), ("job_match", 0.1)]
GOOD = {"ExtractedCriteria": [json.dumps(CRITERIA)], "JobProfileDraft": [json.dumps(PROFILE)], "RubricDraft": [rubric_json(FULL)]}
MISSING_DIM = rubric_json([("technical_skills", 0.6), ("relevant_experience", 0.3), ("qualifications", 0.1)])


# Skip the module when Postgres is unreachable.
@pytest.fixture(autouse=True)
def _require_db():
    try:
        psycopg.connect(CHK_URL, connect_timeout=3).close()
    except Exception:
        pytest.skip("PostgreSQL not reachable")


# Settings writing logs/files to a temp dir.
@pytest.fixture
def settings(tmp_path):
    return Settings(log_dir=tmp_path / "l", error_dir=tmp_path / "e", file_storage_dir=tmp_path / "s",
                    export_dir=tmp_path / "s/e", quarantine_dir=tmp_path / "s/q", rag_corpus_dir=tmp_path / "corpora")


# Create user + job (+ optional policy document), register LG2, and clean everything up afterwards.
@asynccontextmanager
async def environment(settings, script, ingest_policy=False):
    engine = create_async_engine(DB_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm, store = ScriptedLLM(script), KnowledgeStore(fake_embed, settings)
    source_id = None
    async with factory() as s:
        user = User(email=f"{uuid.uuid4()}@test.local", password_hash="x", role="interviewer")
        s.add(user)
        await s.commit()
        job = await jobs.create_job(s, user.id, "Backend role")
        if ingest_policy:
            source_id, _ = await store.ingest_document(
                s, namespace=settings.rag_namespace_company, title="Hiring policy",
                text=f"{uuid.uuid4().hex} Experience equivalence: two years of experience per missing degree year must be stated.")
    orch.registry = GraphRegistry()
    register_job_graph(orch.registry, JobGraphDeps(llm=llm, store=store, session_factory=factory, settings=settings, retry_interval=0.01))
    try:
        yield SimpleNamespace(job=job, llm=llm, store=store, factory=factory, thread=f"job-{job.id}")
    finally:
        async with factory() as s:
            owned = list(await s.scalars(select(Job.id).where(Job.owner_id == user.id)))
            await s.execute(delete(Approval).where(Approval.job_id.in_(owned)))
            for job_id in owned:
                await s.execute(delete(KnowledgeSource).where(KnowledgeSource.source_uri.like(f"job:{job_id}:%")))
            await s.execute(delete(Job).where(Job.owner_id == user.id))
            await s.delete(await s.get(User, user.id))
            await s.commit()
            if source_id:
                await store.delete_source(s, source_id)
        await engine.dispose()


# Start a drafting run.
async def start(env, cp, prompt="Backend Python engineer, 3+ years Python required, PostgreSQL required, Docker nice to have."):
    return await run_graph(WorkKind.JOB_DRAFT, thread_id=env.thread, run_id=str(uuid.uuid4()),
                           input_state={"job_id": str(env.job.id), "recruiter_prompt": prompt}, checkpointer=cp)


# Durable run status.
async def status(env, cp):
    return await run_status(WorkKind.JOB_DRAFT, thread_id=env.thread, checkpointer=cp)


# Latest saved state values of the run.
async def values(env, cp):
    snapshot = await orch.registry.get(WorkKind.JOB_DRAFT, cp).aget_state({"configurable": {"thread_id": env.thread}})
    return snapshot.values


# Do what the API will do: record the decision on the pending approval, then resume the graph.
async def decide_and_resume(env, cp, option, reason=None):
    async with env.factory() as s:
        pending = await s.scalar(select(Approval).where(Approval.job_id == env.job.id, Approval.status == "pending").order_by(Approval.version.desc()))
        await approvals.decide(s, approval_id=pending.id, expected_version=pending.version, option=option, reason=reason)
    return await resume_graph(WorkKind.JOB_DRAFT, thread_id=env.thread, run_id="resume", checkpointer=cp, decision={"option": option, "reason": reason})


# Happy path with a restart at the human gate: draft, wait, resume, activate, posting text, policy used as untrusted data.
def test_draft_gate_restart_activate(settings):
    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, GOOD, ingest_policy=True) as env:
            async with open_checkpointer(CHK_URL) as cp:
                await start(env, cp)
                assert await status(env, cp) == "waiting_human"
            async with open_checkpointer(CHK_URL) as cp2:
                assert await status(env, cp2) == "waiting_human"
                final = await decide_and_resume(env, cp2, "approve")
                assert final["active_version"] == 1 and "Required:" in final["posting_text"]
            async with env.factory() as s:
                job, version = await jobs.get_job(s, env.job.id), await jobs.get_version(s, env.job.id, 1)
                assert job.active_version == 1 and version.status == "active" and version.profile["posting_text"]
                assert abs(sum(d["weight"] for d in version.rubric["dimensions"]) - 1) < 1e-6
                assert version.profile["policy_source_ids"] and version.profile["requirements"][0]["requirement_id"] == "R1"
            assert '<untrusted source="company_policy">' in env.llm.calls_for("JobProfileDraft")[0][1]["content"]

    asyncio.run(scenario())


# Insufficient criteria: the run ends by asking the recruiter, and nothing is drafted or saved.
def test_needs_clarification(settings):
    asked = {"field": "skills", "question": "Which skills are required?", "options": ["Python", "Java", "Both"]}
    script = {"ExtractedCriteria": [json.dumps({"title": "", "requirements": [], "missing_info": ["required skills"], "sufficient": False})],
              "ClarifyingQuestion": [json.dumps(asked)]}

    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, script) as env:
            async with open_checkpointer(CHK_URL) as cp:
                await start(env, cp, prompt="We need someone good.")
                state = await values(env, cp)
                assert state["status"] == "awaiting_recruiter" and state["clarification_question"] == "Which skills are required?"
                assert state["clarification_options"] == ["Python", "Java", "Both"] and state["clarification_field"] == "skills"
                assert await status(env, cp) == "finished" and not env.llm.calls_for("JobProfileDraft")
            async with env.factory() as s:
                assert await jobs.get_version(s, env.job.id, 1) is None

    asyncio.run(scenario())


# An invalid rubric triggers one regeneration that includes the validation error; the second attempt passes.
def test_validation_retry_then_success(settings):
    script = {**GOOD, "RubricDraft": [MISSING_DIM, rubric_json(FULL)]}

    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, script) as env:
            async with open_checkpointer(CHK_URL) as cp:
                await start(env, cp)
                assert await status(env, cp) == "waiting_human"
            assert len(env.llm.calls_for("JobProfileDraft")) == 2 and len(env.llm.calls_for("RubricDraft")) == 2
            assert "must include" in env.llm.calls_for("JobProfileDraft")[1][1]["content"]

    asyncio.run(scenario())


# Repeated invalid drafts end as a retryable failure and save nothing.
def test_validation_exhausted(settings):
    script = {**GOOD, "RubricDraft": [MISSING_DIM]}

    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, script) as env:
            async with open_checkpointer(CHK_URL) as cp:
                result = await start(env, cp)
                assert result["error_category"] == "draft_invalid" and len(env.llm.calls_for("RubricDraft")) == 2
            async with env.factory() as s:
                assert await jobs.get_version(s, env.job.id, 1) is None

    asyncio.run(scenario())


# Criteria that mention a protected characteristic are blocked (N20) even if the model produces them.
def test_fairness_blocks_protected_terms(settings):
    biased = {**PROFILE, "requirements": REQS + [{"text": "Must be under 30 years of age", "kind": "mandatory", "weight": 1}]}
    script = {**GOOD, "JobProfileDraft": [json.dumps(biased)]}

    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, script) as env:
            async with open_checkpointer(CHK_URL) as cp:
                result = await start(env, cp)
                assert result["error_category"] == "draft_invalid" and "'age'" in " ".join(result["validation_errors"])

    asyncio.run(scenario())


# Edit decision: v1 is superseded, v2 is drafted using the feedback, and approving v2 activates it.
def test_edit_then_approve(settings):
    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, GOOD) as env:
            async with open_checkpointer(CHK_URL) as cp:
                await start(env, cp)
                await decide_and_resume(env, cp, "edit", "add Kubernetes as a preferred skill")
                assert await status(env, cp) == "waiting_human" and (await values(env, cp))["draft_version"] == 2
                assert "Kubernetes" in env.llm.calls_for("JobProfileDraft")[1][1]["content"]
                final = await decide_and_resume(env, cp, "approve")
                assert final["active_version"] == 2
            async with env.factory() as s:
                assert (await jobs.get_version(s, env.job.id, 1)).status == "superseded"
                assert (await jobs.get_version(s, env.job.id, 2)).status == "active"

    asyncio.run(scenario())


# Reject decision: the draft is discarded and the job stays without an active version.
def test_reject_discards(settings):
    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, GOOD) as env:
            async with open_checkpointer(CHK_URL) as cp:
                await start(env, cp)
                final = await decide_and_resume(env, cp, "reject")
                assert final["status"] == "discarded"
            async with env.factory() as s:
                assert (await jobs.get_version(s, env.job.id, 1)).status == "discarded"
                assert (await jobs.get_job(s, env.job.id)).active_version is None

    asyncio.run(scenario())


# Approval service: idempotent request, duplicate decision returns same outcome, conflicting/stale/invalid are rejected.
def test_approval_service(settings):
    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, GOOD) as env:
            async with env.factory() as s:
                kw = dict(gate=ApprovalGate.JOB_ACTIVATION, evidence_summary="x", allowed_options=["approve", "reject"], job_id=env.job.id)
                first = await approvals.request_approval(s, version=1, **kw)
                assert (await approvals.request_approval(s, version=1, **kw)).id == first.id
                with pytest.raises(ApprovalError):
                    await approvals.decide(s, approval_id=first.id, expected_version=1, option="edit")
                with pytest.raises(ApprovalError):
                    await approvals.decide(s, approval_id=first.id, expected_version=9, option="approve")
                done = await approvals.decide(s, approval_id=first.id, expected_version=1, option="approve", reason="ok")
                assert done.status == "decided"
                assert (await approvals.decide(s, approval_id=first.id, expected_version=1, option="approve")).id == first.id
                with pytest.raises(ApprovalError):
                    await approvals.decide(s, approval_id=first.id, expected_version=1, option="reject")

    asyncio.run(scenario())


# Weight normalisation always lands on exactly 1 and keeps proportions.
def test_normalize_weights():
    dims = [RubricDimension(key=k, description="d", weight=w, anchors=ANCHORS) for k, w in zip("abcd", (0.5, 0.3, 0.1, 0.2))]
    out = normalize_weights(dims)
    assert abs(sum(d.weight for d in out) - 1) < 1e-9 and out[0].weight > out[1].weight > out[3].weight > out[2].weight
    assert abs(sum(d.weight for d in normalize_weights([d.model_copy(update={"weight": 0.5}) for d in dims])) - 1) < 1e-9




# Approval indexes the JD into the company knowledge base; a later job sees it as a previous JD (style reference).
def test_approved_jd_is_indexed_and_reused(settings):
    unique = {**GOOD, "JobProfileDraft": [json.dumps({**PROFILE, "summary": f"Builds and runs our APIs {uuid.uuid4().hex}."})]}

    # Whole scenario in one event loop (unique JD text, so identical leftovers can never be deduplicated against).
    async def scenario():
        async with environment(settings, unique) as env:
            async with open_checkpointer(CHK_URL) as cp:
                await start(env, cp)
                final = await decide_and_resume(env, cp, "approve")
                assert final["jd_indexed"] is True
            async with env.factory() as s:
                found = list(await s.scalars(select(KnowledgeSource).where(KnowledgeSource.source_uri == f"job:{env.job.id}:v1")))
                assert len(found) == 1 and found[0].title == "Job description: Backend Python Engineer v1"
                second = await jobs.create_job(s, env.job.owner_id, "Another role")
            async with open_checkpointer(CHK_URL) as cp:
                await run_graph(WorkKind.JOB_DRAFT, thread_id=f"job-{second.id}", run_id=str(uuid.uuid4()), checkpointer=cp,
                                input_state={"job_id": str(second.id), "recruiter_prompt": "Backend Python engineer with PostgreSQL"})
            prompt = env.llm.calls_for("JobProfileDraft")[-1][1]["content"]
            assert '<untrusted source="previous_job_descriptions">' in prompt and "[Job description: Backend Python Engineer v1]" in prompt
            assert "The hiring company is Chorolin IT LTD." in prompt

    asyncio.run(scenario())


# Company documents are passed as untrusted company_policy and general company info is always requested.
def test_company_documents_reach_the_prompt(settings):
    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, GOOD) as env:
            async with env.factory() as s:
                about, _ = await env.store.ingest_document(
                    s, namespace=settings.rag_namespace_company, title="Company overview",
                    text=f"{uuid.uuid4().hex} Chorolin IT LTD is a software company. Culture: small teams and code review.")
            try:
                async with open_checkpointer(CHK_URL) as cp:
                    await start(env, cp)
                    state = await values(env, cp)
                prompt = env.llm.calls_for("JobProfileDraft")[0][1]["content"]
                assert any(p["title"] == "Company overview" and p["kind"] == "company" for p in state["policy_passages"])
                assert '<untrusted source="company_policy">' in prompt and "[Company overview]" in prompt
            finally:
                async with env.factory() as s:
                    await env.store.delete_source(s, about)

    asyncio.run(scenario())


# If indexing fails (e.g. embedding service down), the approval still succeeds and the failure is reported in state.
def test_index_failure_does_not_block_approval(settings):
    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, GOOD) as env:
            # Simulate an embedding outage at indexing time.
            async def broken(*args, **kwargs):
                raise TransientError("embedding service down")

            env.store.ingest_document = broken
            async with open_checkpointer(CHK_URL) as cp:
                await start(env, cp)
                final = await decide_and_resume(env, cp, "approve")
            assert final["active_version"] == 1 and final["jd_indexed"] is False and not final.get("error")

    asyncio.run(scenario())


# The job-relevance check also covers benefits and the company description.
def test_fairness_covers_benefits(settings):
    biased = {**PROFILE, "benefits": ["Housing allowance for married employees"]}
    script = {**GOOD, "JobProfileDraft": [json.dumps(biased)]}

    # Whole scenario in one event loop.
    async def scenario():
        async with environment(settings, script) as env:
            async with open_checkpointer(CHK_URL) as cp:
                result = await start(env, cp)
                assert result["error_category"] == "draft_invalid" and "'married'" in " ".join(result["validation_errors"])

    asyncio.run(scenario())


def test_company_pdf_is_automatically_available_to_job_drafting(settings):
    corpus = settings.rag_corpus_dir / "company"
    corpus.mkdir(parents=True)
    filename = f"company_information_{uuid.uuid4().hex}.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), f"Chorolin IT LTD develops Python APIs and PostgreSQL software. {uuid.uuid4().hex}")
        doc.save(corpus / filename)
    (corpus / "broken.pdf").write_bytes(b"%PDF-1.4 broken")

    async def scenario():
        async with environment(settings, GOOD) as env:
            try:
                async with open_checkpointer(CHK_URL) as cp:
                    await start(env, cp)
                    assert await status(env, cp) == "waiting_human"
                prompt = env.llm.calls_for("JobProfileDraft")[0][1]["content"]
                assert "company information" in prompt and "[Page 1]" in prompt and "PostgreSQL software" in prompt
                assert await ingest_directory("company", corpus, settings=settings, store=env.store,
                                              session_factory=env.factory) == (2, 0, 1)
            finally:
                async with env.factory() as s:
                    source = await s.scalar(select(KnowledgeSource).where(KnowledgeSource.source_uri == f"file:{filename}"))
                    if source:
                        await env.store.delete_source(s, source.id)
    asyncio.run(scenario())
