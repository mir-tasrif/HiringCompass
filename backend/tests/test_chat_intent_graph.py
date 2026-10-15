"""LG1 intent classification checkpoints IDs and a decision, not conversation text."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver

from app.graphs.chat_intent.graph import build_chat_intent_graph
from tests.helpers import ScriptedLLM


def test_intent_graph_classifies_message_without_checkpointing_conversation_text():
    thread_id, message_id = uuid.uuid4(), uuid.uuid4()
    secret_text = "Please explain the current job status"
    thread = SimpleNamespace(state="approved", title="Engineer")
    messages = [SimpleNamespace(id=message_id, role="user", content=secret_text)]

    class Session:
        async def get(self, model, key):
            return thread

        async def scalars(self, statement):
            return messages

    @asynccontextmanager
    async def session_factory():
        yield Session()

    llm = ScriptedLLM({"TurnIntent": [json.dumps({"action": "conversation"})]})
    graph = build_chat_intent_graph(llm, session_factory, MemorySaver())
    config = {"configurable": {"thread_id": f"chat-intent-{message_id}"}}

    async def run():
        result = await graph.ainvoke({"thread_id": str(thread_id), "message_id": str(message_id)}, config)
        snapshot = await graph.aget_state(config)
        return result, snapshot.values

    result, saved = asyncio.run(run())
    assert result["action"] == "conversation"
    assert set(saved) == {"thread_id", "message_id", "action", "prompt_key", "prompt_version"}
    assert (saved["prompt_key"], saved["prompt_version"]) == ("chat.intent", "1")
    assert secret_text not in repr(saved)
    assert secret_text in llm.calls_for("TurnIntent")[0][-1]["content"]
