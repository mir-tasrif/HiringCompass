"""Create and read recruiter chat threads and prepare their job-intake transcript."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models import ChatMessage, ChatThread
from app.services import jobs

GREETING = "Hi! I'm your HiringCompass assistant. How can I help you today?"


async def create_thread(session: AsyncSession, owner_id: uuid.UUID) -> ChatThread:
    """Create an untitled job conversation and its first assistant message."""
    job = await jobs.create_job(session, owner_id, "Untitled job")
    thread = ChatThread(owner_id=owner_id, job_id=job.id)
    session.add(thread)
    await session.flush()
    session.add(ChatMessage(thread_id=thread.id, role="assistant", content=GREETING, payload={"type": "text"}))
    await session.commit()
    return thread


async def get_owned_thread(session: AsyncSession, thread_id: uuid.UUID, owner_id: uuid.UUID) -> ChatThread:
    """Return only a conversation belonging to the signed-in recruiter."""
    thread = await session.scalar(select(ChatThread).where(ChatThread.id == thread_id, ChatThread.owner_id == owner_id))
    if thread is None:
        raise NotFoundError("Conversation not found.")
    return thread


async def list_threads(session: AsyncSession, owner_id: uuid.UUID) -> list[ChatThread]:
    return list(await session.scalars(select(ChatThread).where(ChatThread.owner_id == owner_id).order_by(ChatThread.updated_at.desc())))


async def list_messages(session: AsyncSession, thread_id: uuid.UUID) -> list[ChatMessage]:
    return list(await session.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at)))


def build_transcript(messages: list[ChatMessage]) -> str:
    """Build the job-intake context passed into the job intelligence graph."""
    lines = []
    for index, message in enumerate(messages):
        kind = (message.payload or {}).get("type")
        previous = messages[index - 1] if index else None
        answered_question = previous is not None and previous.role == "assistant" and (previous.payload or {}).get("type") == "question"
        if message.role == "user" and kind == "text" and ((message.payload or {}).get("job_details", True) or answered_question):
            lines.append(f"Recruiter: {message.content}")
        elif message.role == "assistant" and kind == "question":
            lines.append(f"Assistant asked: {message.content}")
    return "\n".join(lines)
