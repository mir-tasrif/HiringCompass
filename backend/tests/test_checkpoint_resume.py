"""Restart-resume integration tests (WBS 2.2.2). Needs the Postgres from docker compose; skipped otherwise."""

import asyncio
import os
import uuid
from typing import Any

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graphs.common.base_state import RunMeta, ok_or_end
from app.graphs.common.checkpointer import open_checkpointer
from app.graphs.common.node_guard import guarded_node
import app.graphs.orchestrator as orch
from app.graphs.orchestrator import GraphRegistry, GraphSpec, resume_graph, run_graph, run_status
from app.schemas.contracts import WorkKind

DB_URL = os.getenv("CHECKPOINT_DB_URL", "postgresql://hc_user:change_me@localhost:5432/hiringcompass")


# Simulates the whole process dying mid-node (BaseException is not caught by guards or runners).
class SimulatedCrash(BaseException):
    pass


# State shared by both test graphs.
class FlowState(RunMeta):
    value: int
    decision: str


# Skip the module when Postgres is unreachable.
@pytest.fixture(autouse=True)
def _require_db():
    try:
        psycopg.connect(DB_URL, connect_timeout=3).close()
    except Exception:
        pytest.skip("PostgreSQL not reachable")


# Build a fresh registry for each simulated process lifetime.
def new_registry(builder) -> None:
    orch.registry = GraphRegistry()
    orch.registry.register(GraphSpec(WorkKind.APPLICATION_SCREENING, builder))


# Crash scenario: a node dies mid-run; after "restart" only unfinished work re-runs.
def test_resume_after_crash():
    runs = {"a": 0, "b": 0, "c": 0, "crash": True}

    # Graph a -> b -> c where b crashes once.
    def build(cp: Any):
        @guarded_node("a")
        async def a(state: FlowState) -> dict:
            runs["a"] += 1
            return {"value": 1}

        @guarded_node("b")
        async def b(state: FlowState) -> dict:
            runs["b"] += 1
            if runs["crash"]:
                runs["crash"] = False
                raise SimulatedCrash()
            return {"value": state["value"] + 1}

        @guarded_node("c")
        async def c(state: FlowState) -> dict:
            runs["c"] += 1
            return {"value": state["value"] + 1}

        g = StateGraph(FlowState)
        g.add_node("a", a)
        g.add_node("b", b)
        g.add_node("c", c)
        g.add_edge(START, "a")
        g.add_conditional_edges("a", ok_or_end("b"))
        g.add_conditional_edges("b", ok_or_end("c"))
        g.add_edge("c", END)
        return g.compile(checkpointer=cp)

    thread = f"test-{uuid.uuid4()}"

    # Process 1 starts the run and "dies" inside node b.
    async def first_process():
        new_registry(build)
        async with open_checkpointer(DB_URL) as cp:
            with pytest.raises(SimulatedCrash):
                await run_graph(WorkKind.APPLICATION_SCREENING, thread_id=thread, run_id="r1", input_state={}, checkpointer=cp)
            assert await run_status(WorkKind.APPLICATION_SCREENING, thread_id=thread, checkpointer=cp) == "resumable"

    # Process 2 starts fresh (new pool, new compiled graph) and resumes from the saved checkpoint.
    async def second_process():
        new_registry(build)
        async with open_checkpointer(DB_URL) as cp:
            result = await resume_graph(WorkKind.APPLICATION_SCREENING, thread_id=thread, run_id="r1", checkpointer=cp)
            status = await run_status(WorkKind.APPLICATION_SCREENING, thread_id=thread, checkpointer=cp)
            return result, status

    asyncio.run(first_process())
    result, status = asyncio.run(second_process())
    assert result["value"] == 3 and status == "finished"
    assert runs == {"a": 1, "b": 2, "c": 1, "crash": False}  # node a was NOT repeated


# Human-review scenario: a paused gate survives restart and resumes with the stored decision.
def test_waiting_gate_survives_restart():
    runs = {"prepare": 0}

    # Graph prepare -> gate (interrupt) -> finish.
    def build(cp: Any):
        @guarded_node("prepare")
        async def prepare(state: FlowState) -> dict:
            runs["prepare"] += 1
            return {"value": 10}

        @guarded_node("gate")
        async def gate(state: FlowState) -> dict:
            answer = interrupt({"gate": "f2_progression", "value": state["value"]})
            return {"decision": answer}

        @guarded_node("finish")
        async def finish(state: FlowState) -> dict:
            return {"value": state["value"] + (1 if state["decision"] == "approve" else 0)}

        g = StateGraph(FlowState)
        g.add_node("prepare", prepare)
        g.add_node("gate", gate)
        g.add_node("finish", finish)
        g.add_edge(START, "prepare")
        g.add_edge("prepare", "gate")
        g.add_edge("gate", "finish")
        g.add_edge("finish", END)
        return g.compile(checkpointer=cp)

    thread = f"test-{uuid.uuid4()}"

    # Process 1 runs until the human gate and then stops.
    async def first_process():
        new_registry(build)
        async with open_checkpointer(DB_URL) as cp:
            await run_graph(WorkKind.APPLICATION_SCREENING, thread_id=thread, run_id="r2", input_state={}, checkpointer=cp)
            return await run_status(WorkKind.APPLICATION_SCREENING, thread_id=thread, checkpointer=cp)

    # Process 2 sees the gate still waiting, then resumes it with the human's decision.
    async def second_process():
        new_registry(build)
        async with open_checkpointer(DB_URL) as cp:
            waiting = await run_status(WorkKind.APPLICATION_SCREENING, thread_id=thread, checkpointer=cp)
            result = await resume_graph(WorkKind.APPLICATION_SCREENING, thread_id=thread, run_id="r2", checkpointer=cp, decision="approve")
            return waiting, result

    assert asyncio.run(first_process()) == "waiting_human"
    waiting, result = asyncio.run(second_process())
    assert waiting == "waiting_human" and result["value"] == 11 and result["decision"] == "approve"
    assert runs["prepare"] == 1