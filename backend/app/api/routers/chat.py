"""Assistant chat endpoints (interviewer only): threads, messages and draft decisions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_interviewer
from app.db.models import ChatMessage, ChatThread, User
from app.db.session import get_session
from app.services import chat
from app.services.chat import ChatRuntime
from app.services.dispatch import spawn

router = APIRouter(prefix="/chat", tags=["chat"])


# One message as shown in the UI.
class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    payload: dict[str, Any] | None
    created_at: datetime


# Conversation list entry.
class ThreadSummary(BaseModel):
    id: uuid.UUID
    title: str
    state: str
    status: str
    updated_at: datetime


# Full conversation with its messages.
class ThreadOut(ThreadSummary):
    job_id: uuid.UUID | None
    messages: list[MessageOut]


# Body of a typed message.
class SendBody(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


# Body of an Approve / Regenerate click; ids come from the draft card so stale clicks are rejected.
class DecisionBody(BaseModel):
    option: Literal["approve", "regenerate"]
    approval_id: uuid.UUID
    version: int


# Background-task runtime created at startup.
def get_runtime(request: Request) -> ChatRuntime:
    return request.app.state.chat_runtime


# Convert an ORM message.
def _message(m: ChatMessage) -> MessageOut:
    return MessageOut(id=m.id, role=m.role, content=m.content, payload=m.payload, created_at=m.created_at)


# Convert an ORM thread with its messages.
def _thread(t: ChatThread, messages: list[ChatMessage]) -> ThreadOut:
    return ThreadOut(id=t.id, title=t.title, state=t.state, status=t.status, updated_at=t.updated_at, job_id=t.job_id,
                     messages=[_message(m) for m in messages])


# Start a new job conversation.
@router.post("/threads", response_model=ThreadOut, status_code=201)
async def create_thread(user: User = Depends(get_current_interviewer), session: AsyncSession = Depends(get_session)) -> ThreadOut:
    thread = await chat.create_thread(session, user.id)
    return _thread(thread, await chat.list_messages(session, thread.id))


# List the user's conversations.
@router.get("/threads", response_model=list[ThreadSummary])
async def list_threads(user: User = Depends(get_current_interviewer), session: AsyncSession = Depends(get_session)) -> list[ThreadSummary]:
    return [ThreadSummary(id=t.id, title=t.title, state=t.state, status=t.status, updated_at=t.updated_at)
            for t in await chat.list_threads(session, user.id)]


# Read one conversation (the UI polls this while the assistant is working).
@router.get("/threads/{thread_id}", response_model=ThreadOut)
async def get_thread(thread_id: uuid.UUID, user: User = Depends(get_current_interviewer), session: AsyncSession = Depends(get_session)) -> ThreadOut:
    thread = await chat.get_owned_thread(session, thread_id, user.id)
    return _thread(thread, await chat.list_messages(session, thread.id))


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: uuid.UUID, user: User = Depends(get_current_interviewer),
                        session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    thread = await chat.get_owned_thread(session, thread_id, user.id)
    await chat.delete_thread(session, thread)
    return {"deleted": True}


# Send a message; the reply is produced in the background (202).
@router.post("/threads/{thread_id}/messages", response_model=MessageOut, status_code=202)
async def send_message(thread_id: uuid.UUID, body: SendBody, user: User = Depends(get_current_interviewer),
                       session: AsyncSession = Depends(get_session), runtime: ChatRuntime = Depends(get_runtime)) -> MessageOut:
    thread = await chat.get_owned_thread(session, thread_id, user.id)
    message = await chat.begin_turn(session, thread, body.content)
    spawn(chat.run_turn(runtime, thread.id), f"chat-turn-{thread.id}")
    return _message(message)


# Approve or regenerate the draft on screen (202, processed in the background).
@router.post("/threads/{thread_id}/decision", response_model=MessageOut, status_code=202)
async def decide(thread_id: uuid.UUID, body: DecisionBody, user: User = Depends(get_current_interviewer),
                 session: AsyncSession = Depends(get_session), runtime: ChatRuntime = Depends(get_runtime)) -> MessageOut:
    thread = await chat.get_owned_thread(session, thread_id, user.id)
    message = await chat.begin_decision(session, thread, body.option, body.approval_id, body.version)
    spawn(chat.run_decision(runtime, thread.id, body.option), f"chat-decision-{thread.id}")
    return _message(message)
