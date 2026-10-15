"""Coordinate recruiter chat turns, job draft decisions, and background replies."""

from __future__ import annotations

import uuid
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.db.models import Approval, ChatMessage, ChatThread, Job, JobPosting

from app.core.errors import AppError, ApprovalError, ConflictError
from app.core.logging import get_logger, log_exception
from app.graphs.lg2_job_intelligence.deps import JobGraphDeps
from app.graphs.chat_intent.graph import register_chat_intent_graph
from app.graphs.lg2_job_intelligence.drafts import JobEditPlan
from app.graphs.lg2_job_intelligence import prompts as job_prompts
from app.graphs.lg2_job_intelligence.posting import build_posting_text
from app.graphs.orchestrator import GraphRegistry, resume_graph, run_graph, run_status, run_values
from app.schemas.contracts import ApprovalGate, WorkKind
from app.llm.structured import generate_structured, wrap_untrusted
from app.prompts.system import CHAT_CONVERSATION
from app.services import approvals, jobs
from app.services.postings import post_to_discord
from app.services.interview_chat import handle_interview_chat, roster_reply, selected_roster
from app.services.interview_scheduling import handle_interview_invitation as _handle_interview_invitation
from app.services.chat_threads import create_thread, get_owned_thread, list_threads, list_messages, build_transcript
from app.services.chat_job_status import job_activity_context as _job_activity_context
from app.services.chat_candidate_actions import (_handle_candidate_work_request,
                                                  _handle_scoring_work_request, _confirm_candidate_work)
# Compatibility for callers that imported this time parser from the chat module.
from app.services.interview_scheduling import chat_interview_time as _chat_interview_time


logger = get_logger("services.chat")

MAX_QUESTIONS = 6
REGENERATE_FEEDBACK = "Regenerate: write a fresh alternative version with different wording and structure, keeping every requirement."
_BUSY = "I'm still working on your previous message."
_FAILED = "Sorry, something went wrong while working on that. Please try again."


def _explicit_job_request(message: str) -> bool:
    """Keep clear hiring requests out of free-form conversation even if intent classification slips."""
    text = message.strip().lower()
    if re.search(r"\b(?:i|we)\s+(?:want|need|plan|would like)\s+to\s+(?:hire|recruit)\b", text):
        return True
    if re.search(r"\b(?:hiring|recruiting)\s+(?:a|an|for)\b", text):
        return True
    if re.search(r"\b(?:create|draft|write|generate|prepare|make)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:job description|job posting|jd)\b", text):
        return True
    return bool(re.search(r"\b(?:role|position|job title)\s*:\s*[^\n]{3,}", text))


def _is_job_status_question(message: str) -> bool:
    text = message.strip().lower().replace("’", "'")
    return bool(re.search(
        r"\b(?:what(?:'s| is)\s+(?:the\s+)?(?:updates?|status|progress|next|now)|what\s+now|whats\s+now|"
        r"(?:give|show|tell)\s+me\s+(?:the\s+)?(?:job\s+)?(?:updates?|status)|"
        r"how many\s+(?:cvs|resumes|applications)|"
        r"(?:statuses|status|updates?|progress)\s+(?:of|for)\s+(?:the\s+)?jobs?)\b",
        text,
    ))


# Everything a background turn needs: graph dependencies, the durable checkpointer, and a session factory.
@dataclass(frozen=True)
class ChatRuntime:
    deps: JobGraphDeps
    checkpointer: Any
    session_factory: async_sessionmaker[AsyncSession]
    intent_graph: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        registry = GraphRegistry()
        register_chat_intent_graph(registry, self.deps.llm, self.session_factory)
        object.__setattr__(self, "intent_graph", registry.get(WorkKind.CHAT_INTENT, self.checkpointer))


async def delete_thread(session: AsyncSession, thread: ChatThread) -> None:
    locked = await _lock_idle(session, thread)
    await session.delete(locked)
    await session.commit()


# The newest pending job-activation approval for a job (the draft awaiting a decision).
async def _pending_approval(session: AsyncSession, job_id: uuid.UUID | None) -> Approval | None:
    return await session.scalar(
        select(Approval).where(Approval.job_id == job_id, Approval.gate == ApprovalGate.JOB_ACTIVATION.value, Approval.status == "pending")
        .order_by(Approval.version.desc()).limit(1)
    )


# Lock the thread; an approved job remains open for conversation and subsequent work.
async def _lock_idle(session: AsyncSession, thread: ChatThread) -> ChatThread:
    locked = await session.get(ChatThread, thread.id, with_for_update=True, populate_existing=True)
    if locked.status != "idle":
        raise ConflictError(_BUSY)
    return locked


# Save the recruiter's message and mark the thread busy (the reply is produced in the background).
async def begin_turn(session: AsyncSession, thread: ChatThread, text: str) -> ChatMessage:
    locked = await _lock_idle(session, thread)
    message = ChatMessage(thread_id=locked.id, role="user", content=text, payload={"type": "text"})
    session.add(message)
    locked.status = "working"
    await session.commit()
    return message


# Validate a button click against the draft currently waiting, save it as a message and mark the thread busy.
async def begin_decision(session: AsyncSession, thread: ChatThread, option: str, approval_id: uuid.UUID, version: int) -> ChatMessage:
    locked = await _lock_idle(session, thread)
    if locked.state != "awaiting_decision":
        raise ConflictError("There is no draft waiting for a decision.")
    approval = await _pending_approval(session, locked.job_id)
    if approval is None or approval.id != approval_id or approval.version != version:
        raise ApprovalError("This draft is out of date; please review the latest one.")
    message = ChatMessage(thread_id=locked.id, role="user", content="Approve" if option == "approve" else "Regenerate",
                          payload={"type": "decision", "option": option})
    session.add(message)
    locked.status = "working"
    await session.commit()
    return message


# Background: produce the assistant reply to a typed message.
async def run_turn(runtime: ChatRuntime, thread_id: uuid.UUID) -> None:
    await _run(runtime, thread_id, lambda s, t, text: _handle_text(s, runtime, t))


# Background: apply an Approve / Regenerate click.
async def run_decision(runtime: ChatRuntime, thread_id: uuid.UUID, option: str) -> None:
    await _run(runtime, thread_id, lambda s, t, text: _decide(s, runtime, t, "approve" if option == "approve" else "edit",
                                                              None if option == "approve" else REGENERATE_FEEDBACK))


# Shared wrapper: run the work, always store an assistant reply (friendly on failure), and release the thread.
async def _run(runtime: ChatRuntime, thread_id: uuid.UUID, work: Callable[[AsyncSession, ChatThread, str], Awaitable[tuple[str, dict[str, Any]]]]) -> None:
    async with runtime.session_factory() as session:
        thread = await session.get(ChatThread, thread_id)
        try:
            content, payload = await work(session, thread, "")
        except Exception as exc:
            await session.rollback()
            log_exception(logger, "chat turn failed", exc, thread_id=str(thread_id))
            content, payload = (str(exc) if isinstance(exc, AppError) else _FAILED), {"type": "error"}
        thread = await session.get(ChatThread, thread_id, populate_existing=True)
        session.add(ChatMessage(thread_id=thread_id, role="assistant", content=content, payload=payload))
        thread.status = "idle"
        await session.commit()


# Route conversation separately from job creation and explicit changes.
async def _handle_text(session: AsyncSession, runtime: ChatRuntime, thread: ChatThread) -> tuple[str, dict[str, Any]]:
    messages = await list_messages(session, thread.id)
    last_user = next(m for m in reversed(messages) if m.role == "user")
    simple = last_user.content.strip().lower().rstrip(".!?")
    previous_assistant = messages[-2] if len(messages) > 1 and messages[-2].role == "assistant" else None
    pending_edit_question = (previous_assistant is not None and
                             (previous_assistant.payload or {}).get("field") == "job_edit_requirement_details")
    if thread.state == "awaiting_decision" and pending_edit_question:
        original = (previous_assistant.payload or {}).get("original_request", "")
        return await _decide(session, runtime, thread, "edit", f"{original}\nRecruiter clarification: {last_user.content}")
    if simple in {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "thanks", "thank you"}:
        last_user.payload = {**(last_user.payload or {}), "job_details": False}
        await session.commit()
        reply = "You're welcome! What else can I help with?" if simple in {"thanks", "thank you"} else "Hi! How can I help you today?"
        return reply, {"type": "text"}
    if simple in {"approve", "approved", "i approve", "looks good, approve"}:
        if thread.state == "awaiting_decision":
            return await _decide(session, runtime, thread, "approve", None)
        return "There is no draft ready to approve yet. Ask me to create a job description first.", {"type": "text"}
    if _is_job_status_question(last_user.content):
        last_user.payload = {**(last_user.payload or {}), "job_details": False}
        await session.commit()
        return await _job_update(session, runtime, thread)
    previous_payload = (previous_assistant.payload or {}) if previous_assistant else {}
    batch_action = await handle_interview_chat(session, runtime.deps.settings, thread.owner_id,
                                               thread.job_id, last_user.content, previous_payload)
    if batch_action is not None:
        last_user.payload = {**(last_user.payload or {}), "job_details": False}
        await session.commit()
        return batch_action
    if re.search(r"\b(?:already|have)\b.*\b(?:reviewed|selected)\b.*\binterview\b", last_user.content, re.I):
        job = await session.scalar(select(Job).where(Job.id == thread.job_id, Job.owner_id == thread.owner_id)) if thread.job_id else None
        if job and job.active_version is not None:
            roster = await selected_roster(session, thread.owner_id, job.id)
            reply, payload = roster_reply(job, roster)
            return "I checked the saved candidate stages for this job. " + reply, payload
    if re.search(r"\bmove\b.*\binterview\s+selection\b", last_user.content, re.I):
        job = await session.scalar(select(Job).where(Job.id == thread.job_id, Job.owner_id == thread.owner_id)) if thread.job_id else None
        if job and job.active_version is not None:
            roster = await selected_roster(session, thread.owner_id, job.id)
            reply, payload = roster_reply(job, roster)
            return ("Interview selection is saved through the recruiter decision in Review. " + reply, payload)
    interview_action = await _handle_interview_invitation(session, runtime, thread, last_user.content, messages)
    if interview_action is not None:
        last_user.payload = {**(last_user.payload or {}), "job_details": False}
        await session.commit()
        return interview_action
    confirmation = await _confirm_candidate_work(session, thread, messages, simple)
    if confirmation is not None:
        last_user.payload = {**(last_user.payload or {}), "job_details": False}
        await session.commit()
        return confirmation
    scoring_work = await _handle_scoring_work_request(session, thread, last_user.content, messages)
    if scoring_work is not None:
        last_user.payload = {**(last_user.payload or {}), "job_details": False}
        await session.commit()
        return scoring_work
    cv_work = await _handle_candidate_work_request(session, thread, last_user.content, messages)
    if cv_work is not None:
        last_user.payload = {**(last_user.payload or {}), "job_details": False}
        await session.commit()
        return cv_work
    previous_assistant = next((m for m in reversed(messages[:-1]) if m.role == "assistant"), None)
    if thread.state == "approved":
        was_offered = bool(previous_assistant and (
            (previous_assistant.payload or {}).get("type") == "posting_offer" or
            ((previous_assistant.payload or {}).get("type") == "posting_result" and
             (previous_assistant.payload or {}).get("status") == "failed") or
            (previous_assistant.payload or {}).get("posting_available")
        ))
        confirms_offer = was_offered and (
            simple in {"yes", "yes please", "sure", "agreed", "go ahead", "do it", "post it", "post", "discord"} or
            bool(re.fullmatch(r"(?:yes[, ]+)?(?:please[, ]+)?(?:post|publish|send|share)(?: it)?", simple))
        )
        wants_post = _requests_discord_post(last_user.content) or confirms_offer
        if wants_post:
            last_user.payload = {**(last_user.payload or {}), "job_details": False}
            await session.commit()
            return await _post_discord(session, runtime, thread)
        if simple in {"what next", "what should i do next", "what can i do next", "next steps", "what's next", "whats next"}:
            last_user.payload = {**(last_user.payload or {}), "job_details": False}
            await session.commit()
            return await _posting_offer(session, runtime, thread)
    history = [{"role": m.role, "content": m.content} for m in messages[-20:]]
    previous = messages[-2] if len(messages) > 1 else None
    answering_question = (previous is not None and previous.role == "assistant" and
                          (previous.payload or {}).get("type") == "question" and
                          not last_user.content.strip().endswith("?"))
    pending_draft_edit = (thread.state == "awaiting_decision" and bool(re.search(
        r"\b(?:add|include|remove|delete|change|update|modify|rewrite|regenerate|replace)\b",
        last_user.content, re.I,
    )))
    if pending_draft_edit:
        action = "edit_draft"
    elif _explicit_job_request(last_user.content) or answering_question:
        action = "job_details"
    else:
        intent_state = await runtime.intent_graph.ainvoke(
            {"thread_id": str(thread.id), "message_id": str(last_user.id)},
            {"configurable": {"thread_id": f"chat-intent-{last_user.id}"}},
        )
        action = intent_state["action"]
    if thread.job_id and thread.state == "approved" and action != "conversation":
        linked_job = await session.scalar(select(Job).where(Job.id == thread.job_id, Job.owner_id == thread.owner_id))
        if linked_job is not None and linked_job.archived_at is not None:
            last_user.payload = {**(last_user.payload or {}), "job_details": False}
            await session.commit()
            return "This job is in Expired Jobs and its chat is read-only. Start a new job chat to create or work on an active job.", {"type": "text"}
    last_user.payload = {**(last_user.payload or {}), "job_details": action != "conversation"}
    await session.commit()
    if action == "conversation":
        return await _conversation(session, runtime, thread, messages, last_user.content)
    if thread.state == "awaiting_decision":
        if action != "edit_draft":
            return "A draft is waiting for your review. You can approve it or ask me to change it.", {"type": "text"}
        return await _decide(session, runtime, thread, "edit", last_user.content)
    if thread.state == "approved" and action == "job_details":
        return "To create another job, open a new job chat. We can keep discussing this approved job here.", {"type": "text"}
    # An explicit edit to an approved job produces a new draft, leaving the active version intact.
    if thread.state == "approved":
        thread.state = "interviewing"
    questions = sum(1 for m in messages if (m.payload or {}).get("type") == "question")
    thread.run_count += 1
    thread.active_run = f"job-{thread.job_id}-r{thread.run_count}"
    await session.commit()
    result = await run_graph(
        WorkKind.JOB_DRAFT, thread_id=thread.active_run, run_id=str(uuid.uuid4()), checkpointer=runtime.checkpointer,
        input_state={"job_id": str(thread.job_id), "recruiter_prompt": build_transcript(messages), "force_proceed": questions >= MAX_QUESTIONS},
    )
    return await _interpret(session, runtime, thread, result)


async def _job_update(
    session: AsyncSession,
    runtime: ChatRuntime,
    thread: ChatThread,
) -> tuple[str, dict[str, Any]]:
    activity = await _job_activity_context(
        session,
        thread,
        posting_configured=bool(runtime.deps.settings.discord_webhook_url.strip()),
    )
    if not activity:
        return "There are no approved jobs to report yet.", {"type": "job_update", "jobs": []}
    return "Here is the latest status for your approved job(s):", {"type": "job_update", "jobs": activity}


async def _conversation(session: AsyncSession, runtime: ChatRuntime, thread: ChatThread,
                        messages: list[ChatMessage], query: str) -> tuple[str, dict[str, Any]]:
    history = [{"role": m.role, "content": m.content} for m in messages[-20:]]
    job = await jobs.get_job(session, thread.job_id) if thread.job_id else None
    version = None
    if job and job.active_version:
        version = await jobs.get_version(session, job.id, job.active_version)
    draft = await _pending_approval(session, thread.job_id)
    if draft:
        version = await jobs.get_version(session, thread.job_id, draft.version)
    context = {"job_id": str(thread.job_id), "state": thread.state, "title": thread.title,
               "profile": version.profile if version else None, "rubric": version.rubric if version else None}
    context["job_activity"] = await _job_activity_context(
        session, thread, posting_configured=bool(runtime.deps.settings.discord_webhook_url.strip())
    )
    try:
        hits = await runtime.deps.store.search(session, namespace=runtime.deps.settings.rag_namespace_company, query=query, top_k=4)
        context["company_knowledge"] = [{"title": h.title, "content": h.content} for h in hits]
    except AppError:
        await session.rollback()
        context["company_knowledge"] = []
        context["knowledge_unavailable"] = True
    system = CHAT_CONVERSATION.text + wrap_untrusted("saved_job_and_company_knowledge", json.dumps(context, default=str))
    answer = await runtime.deps.llm.chat([{"role": "system", "content": system}, *history])
    return answer, {"type": "text"}


def _requests_discord_post(message: str) -> bool:
    text = message.strip().lower()
    return bool(re.match(
        r"^(?:(?:yes|sure|please|agreed|go ahead)[, ]+)*(?:please\s+)?(?:post|publish|send|share)\b.*\bdiscord\b",
        text,
    ))


async def _post_discord(session: AsyncSession, runtime: ChatRuntime, thread: ChatThread) -> tuple[str, dict[str, Any]]:
    job = await jobs.get_job(session, thread.job_id) if thread.job_id else None
    if job is None or job.archived_at is not None:
        return "This job is expired and cannot be posted. Restore it from Expired Jobs first.", {"type": "text"}
    try:
        version, already_posted = await post_to_discord(session, runtime.deps.settings, thread.job_id)
    except AppError as exc:
        return str(exc), {"type": "posting_result", "platform": "discord", "status": "failed"}
    verb = "was already posted" if already_posted else "has been posted"
    return (f'"{thread.title}" {verb} to Discord {runtime.deps.settings.discord_channel_name} (version {version}).',
            {"type": "posting_result", "platform": "discord", "status": "posted" if not already_posted else "already_posted",
             "version": version, "channel": runtime.deps.settings.discord_channel_name})


async def _posting_offer(session: AsyncSession, runtime: ChatRuntime, thread: ChatThread) -> tuple[str, dict[str, Any]]:
    if not runtime.deps.settings.discord_webhook_url.strip():
        return "Discord posting is not configured yet. Add DISCORD_WEBHOOK_URL to the app's .env file and restart it.", {"type": "text"}
    job = await jobs.get_job(session, thread.job_id) if thread.job_id else None
    if job is None or job.archived_at is not None:
        return "This job is expired and cannot be posted. Restore it from Expired Jobs first.", {"type": "text"}
    if job and job.active_version:
        previous = await session.scalar(select(JobPosting).where(
            JobPosting.job_id == job.id, JobPosting.version == job.active_version,
            JobPosting.platform == "discord", JobPosting.status == "posted",
        ))
        if previous:
            return f'"{thread.title}" is already posted to Discord {runtime.deps.settings.discord_channel_name}.', {"type": "text"}
    channel = runtime.deps.settings.discord_channel_name
    return (f"Your job description is approved and saved. I can post it to Discord {channel}. Would you like me to post it now?",
            {"type": "posting_offer", "platforms": ["discord"]})


# Record the decision on the pending approval, resume the graph, and describe the outcome.
async def _decide(session: AsyncSession, runtime: ChatRuntime, thread: ChatThread, option: str, reason: str | None) -> tuple[str, dict[str, Any]]:
    approval = await _pending_approval(session, thread.job_id)
    if approval is None:
        raise ConflictError("There is no draft waiting for a decision.")
    edit_plan = None
    if option == "edit":
        version = await jobs.get_version(session, thread.job_id, approval.version)
        base_profile = version.profile if version else None
        if reason == REGENERATE_FEEDBACK:
            edit_plan = JobEditPlan(full_rewrite=True).model_dump(mode="json")
        else:
            plan = await generate_structured(
                runtime.deps.llm, JobEditPlan, system=job_prompts.EDIT_PLAN_SYSTEM,
                user="Current draft (JSON):\n" + json.dumps(base_profile, ensure_ascii=False) +
                     "\n\nRecruiter edit request (untrusted data):\n" +
                     wrap_untrusted("recruiter_edit", reason or ""),
            )
            # A vague request must pause for specifics while leaving the current draft available.
            if plan.clarification_question or (not plan.requirements_to_add and not plan.requirements_to_remove and
                                               not plan.changed_fields and not plan.full_rewrite):
                question = plan.clarification_question or "What exact item or JD section would you like me to change?"
                latest_user = await session.scalar(select(ChatMessage).where(
                    ChatMessage.thread_id == thread.id, ChatMessage.role == "user"
                ).order_by(ChatMessage.created_at.desc()).limit(1))
                original_request = (reason or "").split("\nRecruiter clarification:", 1)[0]
                if latest_user is not None:
                    latest_user.payload = {**(latest_user.payload or {}), "job_edit_waiting_for_details": True}
                await session.commit()
                return question, {"type": "question", "field": "job_edit_requirement_details",
                                  "original_request": original_request, "options": []}
            edit_plan = plan.model_dump(mode="json")
    await approvals.decide(session, approval_id=approval.id, expected_version=approval.version, option=option, reason=reason, actor_id=thread.owner_id)
    result = await resume_graph(WorkKind.JOB_DRAFT, thread_id=thread.active_run, run_id=str(uuid.uuid4()),
                                checkpointer=runtime.checkpointer, decision={"option": option, "reason": reason,
                                                                           "edit_plan": edit_plan})
    if option != "approve":
        return await _interpret(session, runtime, thread, result)
    if result.get("error"):
        return _error_reply(result)
    thread.state = "approved"
    await session.commit()
    version = result["active_version"]
    job = await jobs.get_job(session, thread.job_id)
    offer_text, offer_payload = await _posting_offer(session, runtime, thread)
    return (f'Approved. I saved "{thread.title}" as {job.public_code} (job {thread.job_id}, version {version}). {offer_text}',
            {"type": "approved", "job_id": str(thread.job_id), "job_code": job.public_code, "version": version, "jd_text": result.get("posting_text"),
             "posting_available": offer_payload.get("type") == "posting_offer", "posting_platforms": ["discord"]})


# Friendly assistant text for a failed graph run.
def _error_reply(result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    category = result.get("error_category")
    if category == "draft_invalid":
        return f"I couldn't finish a valid draft. {result['error']} Please adjust your requirements and tell me again.", {"type": "error"}
    if category == "transient":
        return "The AI service is not responding right now. Please try again in a moment.", {"type": "error"}
    return _FAILED, {"type": "error"}


# Turn a finished or paused graph run into the assistant's next message (question, draft card, or error).
async def _interpret(session: AsyncSession, runtime: ChatRuntime, thread: ChatThread, result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if result.get("error"):
        return _error_reply(result)
    state = await run_values(WorkKind.JOB_DRAFT, thread_id=thread.active_run, checkpointer=runtime.checkpointer)
    title = ((state.get("job_profile") or {}).get("title") or (state.get("criteria") or {}).get("title") or "").strip()
    if title:
        thread.title = title[:200]
        job = await jobs.get_job(session, thread.job_id)
        job.title = title[:200]
        await session.commit()
    if state.get("status") == "awaiting_recruiter":
        return state["clarification_question"], {"type": "question", "field": state.get("clarification_field"),
                                                 "options": state.get("clarification_options", [])}
    if await run_status(WorkKind.JOB_DRAFT, thread_id=thread.active_run, checkpointer=runtime.checkpointer) == "waiting_human":
        approval = await _pending_approval(session, thread.job_id)
        thread.state = "awaiting_decision"
        await session.commit()
        profile, settings = state["job_profile"], runtime.deps.settings
        sources = list(dict.fromkeys(p["title"] for p in state.get("policy_passages", [])))
        return ("Here is the draft job description. Approve it, regenerate it, or type what you would like changed.",
                {"type": "draft", "approval_id": str(approval.id), "version": approval.version, "title": profile["title"],
                 "jd_text": build_posting_text(profile, settings.company_name, settings.google_form_link), "sources": sources,
                 "profile": profile, "company_name": settings.company_name, "google_form_link": settings.google_form_link})
    return _FAILED, {"type": "error"}


# After a restart, free threads that were mid-turn and tell the user to resend.
async def recover_interrupted_threads(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        stuck = list(await session.scalars(select(ChatThread).where(ChatThread.status == "working")))
        for thread in stuck:
            thread.status = "idle"
            session.add(ChatMessage(thread_id=thread.id, role="assistant", payload={"type": "error"},
                                    content="My previous task was interrupted by a restart. Please send your last message again."))
        await session.commit()
    return len(stuck)
