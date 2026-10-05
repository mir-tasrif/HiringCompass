"""Execution metadata shared by every graph state, plus the error-to-state mapping."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END

from app.core.errors import AppError


# Metadata present in every graph state (graph states extend this).
class RunMeta(TypedDict, total=False):
    run_id: str
    thread_id: str
    work_id: str
    attempt: int
    error: str | None
    error_category: str | None


# Convert an exception into a state update; unknown exceptions never leak details (N12).
def error_update(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, AppError):
        return {"error": str(exc)[:300], "error_category": exc.category}
    return {"error": "internal error", "error_category": type(exc).__name__}


# Build a conditional-edge router: go to `next_node`, or END when a node recorded an error.
def ok_or_end(next_node: str):
    # Router evaluated after the previous node finishes.
    def _router(state: dict[str, Any]) -> str:
        return END if state.get("error") else next_node

    return _router