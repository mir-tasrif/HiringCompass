"""LG2 job intelligence graph assembly (one run per job draft; nothing is processed until activation)."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.pregel import RetryPolicy

from app.core.errors import TransientError
from app.graphs.common.base_state import ok_or_end
from app.graphs.lg2_job_intelligence.deps import JobGraphDeps
from app.graphs.lg2_job_intelligence.nodes import make_nodes
from app.graphs.lg2_job_intelligence.routing import make_route_after_validate, route_after_extract, route_after_gate
from app.graphs.lg2_job_intelligence.state import JobIntelState
from app.graphs.orchestrator import GraphRegistry, GraphSpec
from app.schemas.contracts import WorkKind


# Compile the LG2 graph with a durable checkpointer.
def build_job_graph(checkpointer: Any, deps: JobGraphDeps):
    nodes = make_nodes(deps)
    retry = RetryPolicy(max_attempts=3, initial_interval=deps.retry_interval, backoff_factor=2.0, jitter=False, retry_on=TransientError)
    llm_nodes = {"extract_criteria", "ask_clarification", "retrieve_policy", "generate_profile", "generate_rubric"}

    g = StateGraph(JobIntelState)
    for name, fn in nodes.items():
        g.add_node(name, fn, retry=retry) if name in llm_nodes else g.add_node(name, fn)

    g.add_edge(START, "load_inputs")
    g.add_conditional_edges("load_inputs", ok_or_end("extract_criteria"))
    g.add_conditional_edges("extract_criteria", route_after_extract)
    g.add_edge("ask_clarification", END)
    g.add_conditional_edges("retrieve_policy", ok_or_end("generate_profile"))
    g.add_conditional_edges("generate_profile", ok_or_end("generate_rubric"))
    g.add_conditional_edges("generate_rubric", ok_or_end("validate_draft"))
    g.add_conditional_edges("validate_draft", make_route_after_validate(deps.max_draft_attempts))
    g.add_edge("fail_draft", END)
    g.add_conditional_edges("save_draft", ok_or_end("await_activation"))
    g.add_conditional_edges("await_activation", route_after_gate)
    g.add_conditional_edges("activate_version", ok_or_end("prepare_posting_text"))
    g.add_edge("prepare_posting_text", "index_for_rag")
    g.add_edge("index_for_rag", END)
    g.add_edge("prepare_edit", "generate_profile")
    g.add_edge("mark_discarded", END)
    return g.compile(checkpointer=checkpointer)


# Register LG2 in a registry; `deps` are bound here so the orchestrator only needs the checkpointer.
def register_job_graph(registry: GraphRegistry, deps: JobGraphDeps) -> None:
    registry.register(GraphSpec(WorkKind.JOB_DRAFT, lambda checkpointer: build_job_graph(checkpointer, deps)))