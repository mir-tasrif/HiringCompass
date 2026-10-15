"""Checkpoint one chat intent decision without duplicating conversation text in graph state."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ChatMessage, ChatThread
from app.graphs.orchestrator import GraphRegistry, GraphSpec
from app.llm.structured import generate_structured
from app.prompts.system import CHAT_INTENT
from app.schemas.contracts import WorkKind


class TurnIntent(BaseModel):
    action: Literal["conversation", "job_details", "edit_draft"]


class ChatIntentState(TypedDict, total=False):
    thread_id: str
    message_id: str
    action: str
    prompt_key: str
    prompt_version: str


def build_chat_intent_graph(llm: Any, session_factory: async_sessionmaker[AsyncSession], checkpointer: Any):
    async def classify(state: ChatIntentState) -> dict[str, str]:
        thread_id = uuid.UUID(state["thread_id"])
        message_id = uuid.UUID(state["message_id"])
        async with session_factory() as session:
            thread = await session.get(ChatThread, thread_id)
            if thread is None:
                raise ValueError("Chat thread was not found")
            messages = list(await session.scalars(
                select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at)
            ))
            if not any(message.id == message_id and message.role == "user" for message in messages):
                raise ValueError("Chat message was not found")
            history = [{"role": message.role, "content": message.content} for message in messages[-20:]]
            prompt_input = {"job_state": thread.state, "job_title": thread.title, "messages": history}
        intent = await generate_structured(llm, TurnIntent, system=CHAT_INTENT.text,
                                           user=json.dumps(prompt_input))
        return {"action": intent.action, "prompt_key": CHAT_INTENT.key,
                "prompt_version": CHAT_INTENT.version}

    graph = StateGraph(ChatIntentState)
    graph.add_node("classify", classify)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", END)
    return graph.compile(checkpointer=checkpointer)


def register_chat_intent_graph(registry: GraphRegistry, llm: Any,
                               session_factory: async_sessionmaker[AsyncSession]) -> None:
    registry.register(GraphSpec(WorkKind.CHAT_INTENT,
        lambda checkpointer: build_chat_intent_graph(llm, session_factory, checkpointer)))
