"""Assistant chat tests: interview questions with options, draft/decision flow, ownership, busy, cap, recovery."""

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.graphs.orchestrator as orch
from app.api.deps import get_current_interviewer
from app.api.errors import register_error_handlers
from app.api.routers.chat import router as chat_router
from app.core.config import Settings
from app.db.models import Approval, ChatMessage, ChatThread, Job, KnowledgeSource, User
from app.graphs.common.checkpointer import open_checkpointer
from app.graphs.lg2_job_intelligence.deps import JobGraphDeps
from app.graphs.lg2_job_intelligence.graph import register_job_graph
from app.graphs.orchestrator import GraphRegistry
from app.rag.store import KnowledgeStore
from app.services import jobs
from app.services.chat import ChatRuntime, build_transcript, recover_interrupted_threads
from tests.helpers import ScriptedLLM, fake_embed
from tests.test_lg2_job_intelligence import CRITERIA, GOOD, PROFILE, REQS

DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://hc_user:change_me@localhost:5432/hiringcompass")
CHK_URL = os.getenv("CHECKPOINT_DB_URL", "postgresql://hc_user:change_me@localhost:5432/hiringcompass")

INSUFFICIENT = json.dumps({"title": "Junior ML Engineer", "requirements": [], "missing_info": ["experience", "education"], "sufficient": False})
ONE_REQ = json.dumps({"title": "Junior ML Engineer", "requirements": [REQS[0]], "missing_info": [], "sufficient": False})
SUFFICIENT = json.dumps({**CRITERIA, "title": "Junior ML Engineer"})
QUESTION = json.dumps({"field": "experience", "question": "How much experience should the candidate have?", "options": ["0 years", "1 year", "2 years"]})
SCRIPT = {**GOOD, "TurnIntent": [json.dumps({"action": "job_details"})],
          "ConversationReply": ["I can help you with this job. Your approved description is saved."],
          "ExtractedCriteria": [INSUFFICIENT, SUFFICIENT], "ClarifyingQuestion": [QUESTION], "JobProfileDraft": [json.dumps({**PROFILE, "title": "Junior ML Engineer"})]}


# Skip the module when Postgres is unreachable.
@pytest.fixture(autouse=True)
def _require_db():
    try:
        psycopg.connect(CHK_URL, connect_timeout=3).close()
    except Exception:
        pytest.skip("PostgreSQL not reachable")


# Run one coroutine on a fresh loop (safe because the test engine uses NullPool).
def run(coro):
    return asyncio.run(coro)


# Test environment: API app with a scripted LLM, accounts, and cleanup of everything created.
class Env:
    def __init__(self, tmp_path, script):
        self.settings = Settings(log_dir=tmp_path / "l", error_dir=tmp_path / "e", file_storage_dir=tmp_path / "s",
                                 export_dir=tmp_path / "s/e", quarantine_dir=tmp_path / "s/q", rag_corpus_dir=tmp_path / "corpora")
        self.engine = create_async_engine(DB_URL, poolclass=NullPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.llm = ScriptedLLM(script)
        self.users = []
        self.current = {}

        env = self

        # App lifespan: durable checkpointer, LG2 registration and the chat runtime, like production.
        @asynccontextmanager
        async def lifespan(app):
            async with open_checkpointer(CHK_URL) as cp:
                deps = JobGraphDeps(llm=env.llm, store=KnowledgeStore(fake_embed, env.settings), session_factory=env.factory,
                                    settings=env.settings, retry_interval=0.01)
                orch.registry = GraphRegistry()
                register_job_graph(orch.registry, deps)
                app.state.chat_runtime = ChatRuntime(deps=deps, checkpointer=cp, session_factory=env.factory)
                yield

        self.app = FastAPI(lifespan=lifespan)
        register_error_handlers(self.app)
        self.app.include_router(chat_router)
        self.app.dependency_overrides[get_current_interviewer] = lambda: env.current["user"]

    # Create an interviewer account and optionally make it the signed-in user.
    def add_user(self, sign_in=True):
        async def make():
            async with self.factory() as s:
                user = User(email=f"{uuid.uuid4().hex}@test.local", password_hash="x", role="interviewer")
                s.add(user)
                await s.commit()
                return user
        user = run(make())
        self.users.append(user)
        if sign_in:
            self.current["user"] = user
        return user

    # Remove all rows created by the test.
    def cleanup(self):
        async def drop():
            async with self.factory() as s:
                ids = [u.id for u in self.users]
                job_ids = list(await s.scalars(select(Job.id).where(Job.owner_id.in_(ids))))
                await s.execute(delete(Approval).where(Approval.job_id.in_(job_ids)))
                for job_id in job_ids:
                    await s.execute(delete(KnowledgeSource).where(KnowledgeSource.source_uri.like(f"job:{job_id}:%")))                
                await s.execute(delete(ChatThread).where(ChatThread.owner_id.in_(ids)))
                await s.execute(delete(Job).where(Job.owner_id.in_(ids)))
                await s.execute(delete(User).where(User.id.in_(ids)))
                await s.commit()
            await self.engine.dispose()
        run(drop())


# Poll a thread until the assistant has finished working.
def wait_idle(client, thread_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/chat/threads/{thread_id}").json()
        if detail["status"] == "idle":
            return detail
        time.sleep(0.1)
    raise AssertionError("assistant did not finish in time")


# Send a message and wait for the reply.
def say(client, thread_id, text):
    response = client.post(f"/chat/threads/{thread_id}/messages", json={"content": text})
    assert response.status_code == 202 and response.json()["role"] == "user"
    return wait_idle(client, thread_id)


# The first answer is a follow-up question with clickable options, and the job/thread get the extracted title.
def test_interview_asks_question_with_options(tmp_path):
    env = Env(tmp_path, SCRIPT)
    env.add_user()
    try:
        with TestClient(env.app) as client:
            created = client.post("/chat/threads")
            assert created.status_code == 201 and created.json()["messages"][0]["role"] == "assistant"
            tid = created.json()["id"]
            detail = say(client, tid, "I want to hire a junior ML engineer")
            last = detail["messages"][-1]
            assert last["payload"] == {"type": "question", "field": "experience", "options": ["0 years", "1 year", "2 years"]}
            assert last["content"] == "How much experience should the candidate have?" and detail["title"] == "Junior ML Engineer"
            assert "Recruiter: I want to hire a junior ML engineer" in env.llm.calls_for("ExtractedCriteria")[0][1]["content"]
    finally:
        env.cleanup()


# Full loop: question -> answer -> draft card -> regenerate -> approve; stale clicks are rejected and the job is saved.
def test_interview_to_draft_regenerate_approve(tmp_path):
    env = Env(tmp_path, SCRIPT)
    user = env.add_user()
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]
            say(client, tid, "I want to hire a junior ML engineer")
            detail = say(client, tid, "0-1 years, B.Sc in Computer Science")
            draft = detail["messages"][-1]
            assert draft["payload"]["type"] == "draft" and "Required:" in draft["payload"]["jd_text"] and detail["state"] == "awaiting_decision"
            assert "Chorolin IT LTD" in draft["payload"]["jd_text"] and "hr@chorolin.com" in draft["payload"]["jd_text"]
            assert isinstance(draft["payload"]["sources"], list)
            first = draft["payload"]
            body = {"option": "regenerate", "approval_id": first["approval_id"], "version": first["version"]}
            assert client.post(f"/chat/threads/{tid}/decision", json=body).status_code == 202
            detail = wait_idle(client, tid)
            second = detail["messages"][-1]["payload"]
            assert second["type"] == "draft" and second["version"] == 2 and second["approval_id"] != first["approval_id"]
            assert "Regenerate" in env.llm.calls_for("JobProfileDraft")[1][1]["content"]
            stale = client.post(f"/chat/threads/{tid}/decision", json={"option": "approve", "approval_id": first["approval_id"], "version": 1})
            assert stale.status_code == 409
            approve = {"option": "approve", "approval_id": second["approval_id"], "version": second["version"]}
            assert client.post(f"/chat/threads/{tid}/decision", json=approve).status_code == 202
            detail = wait_idle(client, tid)
            assert detail["messages"][-1]["payload"]["type"] == "approved" and detail["state"] == "approved"
            env.llm.script["TurnIntent"] = [json.dumps({"action": "conversation"})]
            continued = say(client, tid, "What can we do next?")
            assert continued["state"] == "approved" and continued["messages"][-1]["payload"]["type"] == "text"

            # The approved JD is stored with a unique id and an immutable active version.
            async def check():
                async with env.factory() as s:
                    job = await jobs.get_job(s, uuid.UUID(detail["job_id"]))
                    version = await jobs.get_version(s, job.id, 2)
                    assert job.active_version == 2 and job.title == "Junior ML Engineer" and version.status == "active"
                    assert version.profile["posting_text"] and job.owner_id == user.id
            run(check())
    finally:
        env.cleanup()


# Typing feedback while a draft is waiting regenerates it using that feedback.
def test_typed_feedback_regenerates(tmp_path):
    env = Env(tmp_path, SCRIPT)
    env.add_user()
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]
            say(client, tid, "junior ML engineer")
            say(client, tid, "0-1 years, B.Sc")
            env.llm.script["TurnIntent"] = [json.dumps({"action": "edit_draft"})]
            detail = say(client, tid, "please add Kubernetes as a preferred skill")
            assert detail["messages"][-1]["payload"]["version"] == 2
            assert "Kubernetes" in env.llm.calls_for("JobProfileDraft")[1][1]["content"]
    finally:
        env.cleanup()


def test_greeting_does_not_start_job_questions(tmp_path):
    env = Env(tmp_path, SCRIPT)
    env.add_user()
    try:
        with TestClient(env.app) as client:
            created = client.post("/chat/threads").json()
            assert "How can I help" in created["messages"][0]["content"]
            detail = say(client, created["id"], "hi")
            assert detail["messages"][-1]["payload"] == {"type": "text"}
            assert not env.llm.calls and detail["state"] == "interviewing"
            say(client, created["id"], "I want to hire a junior ML engineer")
            prompt = env.llm.calls_for("ExtractedCriteria")[0][1]["content"]
            assert "Recruiter: hi\n" not in prompt
    finally:
        env.cleanup()


def test_explicit_hiring_request_creates_reviewable_draft_and_approval(tmp_path):
    script = {**SCRIPT, "ExtractedCriteria": [SUFFICIENT]}
    env = Env(tmp_path, script)
    env.add_user()
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]
            detail = say(client, tid, "I want to hire a junior ML engineer with Python and SQL experience")
            draft = detail["messages"][-1]["payload"]
            assert draft["type"] == "draft" and draft["approval_id"] and draft["version"] == 1
            assert draft["profile"]["responsibilities"] and draft["title"] == "Junior ML Engineer"
            assert detail["state"] == "awaiting_decision" and detail["title"] == "Junior ML Engineer"
            assert not env.llm.calls_for("ConversationReply")
            assert client.post(f"/chat/threads/{tid}/decision", json={
                "option": "approve", "approval_id": draft["approval_id"], "version": draft["version"]}).status_code == 202
            approved = wait_idle(client, tid)
            assert approved["state"] == "approved" and approved["title"] == "Junior ML Engineer"
            assert approved["messages"][-1]["payload"]["version"] == 1
            assert approved["job_id"] in approved["messages"][-1]["content"]
    finally:
        env.cleanup()


def test_approval_without_draft_does_not_claim_to_save_job(tmp_path):
    env = Env(tmp_path, SCRIPT)
    env.add_user()
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]
            detail = say(client, tid, "approved")
            assert detail["state"] == "interviewing"
            assert "no draft ready" in detail["messages"][-1]["content"].lower()
            assert not env.llm.calls
    finally:
        env.cleanup()


def test_transcript_recovers_answer_misrouted_in_existing_chat():
    messages = [
        ChatMessage(role="user", content="I want to hire a DevOps architect", payload={"type": "text", "job_details": True}),
        ChatMessage(role="assistant", content="How many years of experience?", payload={"type": "question"}),
        ChatMessage(role="user", content="5 years", payload={"type": "text", "job_details": False}),
        ChatMessage(role="assistant", content="A plain text posting", payload={"type": "text"}),
        ChatMessage(role="user", content="approved", payload={"type": "text", "job_details": False}),
    ]
    transcript = build_transcript(messages)
    assert "Recruiter: 5 years" in transcript
    assert "Recruiter: approved" not in transcript


def test_delete_chat_preserves_approved_job_and_its_version(tmp_path):
    script = {**SCRIPT, "ExtractedCriteria": [SUFFICIENT]}
    env = Env(tmp_path, script)
    owner, other = env.add_user(), env.add_user(sign_in=False)
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]
            draft = say(client, tid, "I want to hire a junior ML engineer")["messages"][-1]["payload"]
            client.post(f"/chat/threads/{tid}/decision", json={
                "option": "approve", "approval_id": draft["approval_id"], "version": draft["version"]})
            approved = wait_idle(client, tid)
            job_id = uuid.UUID(approved["job_id"])
            env.current["user"] = other
            assert client.delete(f"/chat/threads/{tid}").status_code == 404
            env.current["user"] = owner
            assert client.delete(f"/chat/threads/{tid}").status_code == 200
            assert client.get(f"/chat/threads/{tid}").status_code == 404

            async def check():
                async with env.factory() as session:
                    job = await jobs.get_job(session, job_id)
                    version = await jobs.get_version(session, job_id, 1)
                    assert job.active_version == 1 and job.title == "Junior ML Engineer"
                    assert version.status == "active"
                    assert not list(await session.scalars(select(ChatMessage).where(ChatMessage.thread_id == uuid.UUID(tid))))
            run(check())
    finally:
        env.cleanup()


def test_question_about_waiting_draft_does_not_edit_it(tmp_path):
    env = Env(tmp_path, SCRIPT)
    env.add_user()
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]
            say(client, tid, "I want to hire a junior ML engineer")
            draft = say(client, tid, "0-1 years, B.Sc")["messages"][-1]["payload"]
            env.llm.script["TurnIntent"] = [json.dumps({"action": "conversation"})]
            detail = say(client, tid, "Why did you choose these requirements?")
            assert detail["state"] == "awaiting_decision" and len(env.llm.calls_for("JobProfileDraft")) == 1
            prompt = env.llm.calls_for("ConversationReply")[0][0]["content"]
            assert "technical_skills" in prompt and "Junior ML Engineer" in prompt
            assert client.post(f"/chat/threads/{tid}/decision", json={"option": "approve", "approval_id": draft["approval_id"], "version": draft["version"]}).status_code == 202
            assert wait_idle(client, tid)["state"] == "approved"
    finally:
        env.cleanup()


def test_explicit_revision_preserves_active_job_until_approved(tmp_path):
    env = Env(tmp_path, SCRIPT)
    env.add_user()
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]
            say(client, tid, "I want to hire a junior ML engineer")
            first = say(client, tid, "0-1 years, B.Sc")["messages"][-1]["payload"]
            client.post(f"/chat/threads/{tid}/decision", json={"option": "approve", "approval_id": first["approval_id"], "version": first["version"]})
            detail = wait_idle(client, tid)
            env.llm.script["TurnIntent"] = [json.dumps({"action": "edit_draft"})]
            revised = say(client, tid, "Revise the approved job to prefer Kubernetes")
            assert revised["state"] == "awaiting_decision" and revised["messages"][-1]["payload"]["version"] == 2
            async def check_active():
                async with env.factory() as s:
                    job = await jobs.get_job(s, uuid.UUID(detail["job_id"]))
                    assert job.active_version == 1
                    assert (await jobs.get_version(s, job.id, 1)).status == "active"
            run(check_active())
    finally:
        env.cleanup()


# Conversations are private; a busy thread refuses new messages.
def test_ownership_and_busy(tmp_path):
    env = Env(tmp_path, SCRIPT)
    owner, other = env.add_user(), env.add_user(sign_in=False)
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]
            env.current["user"] = other
            assert client.get(f"/chat/threads/{tid}").status_code == 404
            assert client.post(f"/chat/threads/{tid}/messages", json={"content": "hi"}).status_code == 404
            assert client.get("/chat/threads").json() == []
            env.current["user"] = owner
            assert len(client.get("/chat/threads").json()) == 1

            async def make_busy():
                async with env.factory() as s:
                    await s.execute(update(ChatThread).where(ChatThread.id == uuid.UUID(tid)).values(status="working"))
                    await s.commit()
            run(make_busy())
            assert client.post(f"/chat/threads/{tid}/messages", json={"content": "hi"}).status_code == 409
            assert client.post(f"/chat/threads/{tid}/messages", json={"content": "   "}).status_code == 422
    finally:
        env.cleanup()


# After six questions the assistant stops asking and drafts with what it has.
def test_question_cap_forces_draft(tmp_path):
    script = {**SCRIPT, "ExtractedCriteria": [ONE_REQ]}
    env = Env(tmp_path, script)
    env.add_user()
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]

            # Insert six earlier assistant questions.
            async def seed_questions():
                async with env.factory() as s:
                    for i in range(6):
                        s.add(ChatMessage(thread_id=uuid.UUID(tid), role="assistant", content=f"Question {i}", payload={"type": "question", "options": []}))
                    await s.commit()
            run(seed_questions())
            detail = say(client, tid, "junior ML engineer, Python required")
            assert detail["messages"][-1]["payload"]["type"] == "draft"
    finally:
        env.cleanup()


# A restart frees threads that were mid-turn and leaves an explanatory message.
def test_recover_interrupted_threads(tmp_path):
    env = Env(tmp_path, SCRIPT)
    env.add_user()
    try:
        with TestClient(env.app) as client:
            tid = client.post("/chat/threads").json()["id"]

            # Simulate a crash mid-turn.
            async def crash():
                async with env.factory() as s:
                    await s.execute(update(ChatThread).where(ChatThread.id == uuid.UUID(tid)).values(status="working"))
                    await s.commit()
                return await recover_interrupted_threads(env.factory)
            assert run(crash()) == 1
            detail = client.get(f"/chat/threads/{tid}").json()
            assert detail["status"] == "idle" and "interrupted" in detail["messages"][-1]["content"]
    finally:
        env.cleanup()
