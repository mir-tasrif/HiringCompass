"""Skeleton tests (WBS 2.2.1): routing, guard, retry, hand-off validation, error logging."""

import asyncio
import json
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import RetryPolicy

from app.core.config import Settings
from app.core.errors import HandoffValidationError, PermanentError, TransientError
from app.core.logging import setup_logging
from app.graphs.common.base_state import RunMeta, ok_or_end
from app.graphs.common.handoff import from_state, to_state
from app.graphs.common.node_guard import guarded_node
from app.graphs.orchestrator import GraphRegistry, GraphSpec, run_graph
import app.graphs.orchestrator as orch
from app.schemas.contracts import EvidenceRef, JobProfile, RequirementMatch, RequirementStatus, WorkKind


# State for the 3-node smoke graph.
class SmokeState(RunMeta):
    value: int
    tries: int


# Build a smoke graph whose middle node behaves according to `mode`.
def make_builder(mode: str):
    # Compile the graph with the supplied checkpointer.
    def build(checkpointer: Any):
        calls = {"n": 0}  # counts step_b attempts across retries (state is not saved on a failed attempt)

        @guarded_node("step_a")
        async def step_a(state: SmokeState) -> dict:
            return {"value": state.get("value", 0) + 1}

        @guarded_node("step_b")
        async def step_b(state: SmokeState) -> dict:
            calls["n"] += 1
            tries = calls["n"]
            if mode == "permanent":
                raise PermanentError("unsupported input")
            if mode == "transient_always" or (mode == "transient_twice" and tries < 3):
                raise TransientError("provider busy")
            return {"value": state["value"] + 1, "tries": tries}

        @guarded_node("step_c")
        async def step_c(state: SmokeState) -> dict:
            return {"value": state["value"] + 1}

        g = StateGraph(SmokeState)
        retry = RetryPolicy(max_attempts=3, initial_interval=0.01, backoff_factor=1.0, jitter=False, retry_on=TransientError)
        g.add_node("step_a", step_a)
        g.add_node("step_b", step_b, retry=retry)
        g.add_node("step_c", step_c)
        g.add_edge(START, "step_a")
        g.add_conditional_edges("step_a", ok_or_end("step_b"))
        g.add_conditional_edges("step_b", ok_or_end("step_c"))
        g.add_edge("step_c", END)
        return g.compile(checkpointer=checkpointer)

    return build


# Run the smoke graph with a fresh registry and in-memory checkpointer.
def run(mode: str) -> dict:
    orch.registry = GraphRegistry()
    orch.registry.register(GraphSpec(WorkKind.JOB_DRAFT, make_builder(mode)))
    return asyncio.run(run_graph(WorkKind.JOB_DRAFT, thread_id="t1", run_id="r1", input_state={"value": 0}, checkpointer=MemorySaver()))


# Route logging to a temp directory for each test.
@pytest.fixture(autouse=True)
def _logging(tmp_path):
    s = Settings(log_dir=tmp_path / "logs", error_dir=tmp_path / "errors", file_storage_dir=tmp_path / "s",
                 export_dir=tmp_path / "s/e", quarantine_dir=tmp_path / "s/q")
    setup_logging("test", s)
    return s


# Happy path: all three nodes run in order.
def test_success_path():
    result = run("ok")
    assert result["value"] == 3 and not result.get("error")


# Permanent error: run stops before step_c and records the category.
def test_permanent_error_ends_run(_logging):
    result = run("permanent")
    assert result["error_category"] == "permanent" and result["value"] == 1
    line = (_logging.error_dir / "test.error.log").read_text().splitlines()[0]
    assert json.loads(line)["node"] == "step_b"


# Transient errors are retried by the RetryPolicy and then succeed.
def test_transient_retried_then_ok():
    result = run("transient_twice")
    assert result["value"] == 3 and result["tries"] == 3


# Retry exhaustion becomes a failed state, not a crash.
def test_transient_exhausted_returns_error():
    result = run("transient_always")
    assert result["error_category"] == "transient"


# Hand-off contracts: valid round-trip and rejection of malformed payloads without leaking values.
def test_handoff_validation():
    m = RequirementMatch(requirement_id="R1", status=RequirementStatus.MET, explanation="ok",
                         evidence=[EvidenceRef(source="cv", ref_id="d1", page=1)])
    assert from_state(RequirementMatch, to_state(m)) == m
    with pytest.raises(HandoffValidationError) as exc:
        from_state(JobProfile, {"title": "secret title"})
    assert "secret title" not in str(exc.value) and "job_id" in str(exc.value)