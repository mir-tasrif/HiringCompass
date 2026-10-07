"""Orchestrator: graph registry plus safe run / resume / status helpers (durable via the checkpointer)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langgraph.types import Command

from app.core.logging import bind_context, get_logger, log_exception
from app.graphs.common.base_state import error_update
from app.schemas.contracts import WorkKind

logger = get_logger("graph.orchestrator")


# Declares how to build one compiled graph; `build` receives the checkpointer.
@dataclass(frozen=True)
class GraphSpec:
    kind: WorkKind
    build: Callable[[Any], Any]


# Registry mapping work kinds to graph builders; compiled graphs are cached per checkpointer.
class GraphRegistry:
    def __init__(self) -> None:
        self._specs: dict[WorkKind, GraphSpec] = {}
        self._compiled: dict[tuple[WorkKind, int], Any] = {}

    # Register a graph spec; duplicate kinds are a programming error.
    def register(self, spec: GraphSpec) -> None:
        if spec.kind in self._specs:
            raise ValueError(f"graph already registered: {spec.kind.value}")
        self._specs[spec.kind] = spec

    # Return the compiled graph for a kind and checkpointer, compiling once.
    def get(self, kind: WorkKind, checkpointer: Any) -> Any:
        if kind not in self._specs:
            raise KeyError(f"no graph registered for {kind.value}")
        key = (kind, id(checkpointer))
        if key not in self._compiled:
            self._compiled[key] = self._specs[kind].build(checkpointer)
        return self._compiled[key]

    # Registered kinds (used by the worker and health checks).
    def kinds(self) -> list[WorkKind]:
        return list(self._specs)


registry = GraphRegistry()


# Shared executor: invoke the graph and convert any failure into an error state (never a crash).
async def _execute(kind: WorkKind, thread_id: str, run_id: str, graph_input: Any, checkpointer: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    config = {"configurable": {"thread_id": thread_id}}
    with bind_context(graph=kind.value, run_id=run_id, thread_id=thread_id):
        try:
            result = await registry.get(kind, checkpointer).ainvoke(graph_input, config)
            logger.info("graph finished", extra={"failed": bool(result.get("error"))})
            return result
        except Exception as exc:
            log_exception(logger, "graph run failed", exc)
            return {**fallback, **error_update(exc)}


# Start a new run under a durable thread_id.
async def run_graph(kind: WorkKind, *, thread_id: str, run_id: str, input_state: dict[str, Any], checkpointer: Any) -> dict[str, Any]:
    state_in = {**input_state, "run_id": run_id, "thread_id": thread_id}
    return await _execute(kind, thread_id, run_id, state_in, checkpointer, state_in)


# Continue a run from its last checkpoint; pass `decision` to answer a pending human-review gate.
async def resume_graph(kind: WorkKind, *, thread_id: str, run_id: str, checkpointer: Any, decision: Any = None) -> dict[str, Any]:
    graph_input = Command(resume=decision) if decision is not None else None
    return await _execute(kind, thread_id, run_id, graph_input, checkpointer, {"run_id": run_id, "thread_id": thread_id})


# Report a run's durable status: not_found | finished | waiting_human | resumable.
async def run_status(kind: WorkKind, *, thread_id: str, checkpointer: Any) -> str:
    snapshot = await registry.get(kind, checkpointer).aget_state({"configurable": {"thread_id": thread_id}})
    if not snapshot.values and not snapshot.next:
        return "not_found"
    if not snapshot.next:
        return "finished"
    if any(task.interrupts for task in snapshot.tasks):
        return "waiting_human"
    return "resumable"




# Latest saved state values of a run (what the graph has produced so far, including while it waits).
async def run_values(kind: WorkKind, *, thread_id: str, checkpointer: Any) -> dict[str, Any]:
    snapshot = await registry.get(kind, checkpointer).aget_state({"configurable": {"thread_id": thread_id}})
    return dict(snapshot.values)