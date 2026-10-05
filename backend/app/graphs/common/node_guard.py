"""Node-level error interception: logs every failure to errors/ and keeps graphs fail-safe."""

from __future__ import annotations

import functools
import inspect
import time
from typing import Any, Awaitable, Callable

from langgraph.errors import GraphBubbleUp

from app.core.errors import TransientError
from app.core.logging import bind_context, get_logger, log_exception
from app.graphs.common.base_state import error_update

logger = get_logger("graph.node")


# Decorator for async graph nodes.
# Success: logs duration. TransientError: logged then re-raised so LangGraph's RetryPolicy retries.
# GraphBubbleUp (interrupt/pause signals) is re-raised untouched so human-review gates work.
# Any other exception: logged to errors/ and returned as an error state update (routers then end the run).
def guarded_node(name: str | None = None) -> Callable:
    def decorator(func: Callable[..., Awaitable[dict[str, Any]]]) -> Callable[..., Awaitable[dict[str, Any]]]:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(f"{func.__name__} must be async")
        node = name or func.__name__

        # Wrapper executed by LangGraph in place of the raw node function.
        @functools.wraps(func)
        async def wrapper(state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            started = time.perf_counter()
            with bind_context(node=node, run_id=state.get("run_id"), thread_id=state.get("thread_id")):
                try:
                    result = await func(state, *args, **kwargs)
                    logger.info("node ok", extra={"duration_ms": round((time.perf_counter() - started) * 1000, 1)})
                    return result
                except GraphBubbleUp:
                    raise
                except TransientError as exc:
                    log_exception(logger, f"node '{node}' transient failure", exc)
                    raise
                except Exception as exc:
                    log_exception(logger, f"node '{node}' failed", exc)
                    return error_update(exc)

        return wrapper

    return decorator