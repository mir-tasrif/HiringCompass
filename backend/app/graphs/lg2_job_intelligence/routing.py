"""LG2 conditional-edge routers."""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END


# After criteria extraction: stop on error, ask for clarification, or continue to policy retrieval.
def route_after_extract(state: dict[str, Any]) -> str:
    if state.get("error"):
        return END
    return "retrieve_policy" if state.get("criteria_sufficient") else "ask_clarification"


# After validation: save when valid, regenerate while attempts remain, otherwise fail.
def make_route_after_validate(max_attempts: int) -> Callable[[dict[str, Any]], str]:
    # Router closed over the attempt limit.
    def route(state: dict[str, Any]) -> str:
        if state.get("error"):
            return END
        if not state.get("validation_errors"):
            return "save_draft"
        return "generate_profile" if state.get("retry_count", 0) < max_attempts else "fail_draft"

    return route


# After the human gate: activate, redraft with feedback, or discard.
def route_after_gate(state: dict[str, Any]) -> str:
    if state.get("error"):
        return END
    return {"approve": "activate_version", "edit": "prepare_edit", "reject": "mark_discarded"}[state["decision"]]