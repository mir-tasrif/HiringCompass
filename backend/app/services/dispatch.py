"""Background execution seam: runs long graph work off the request path and keeps task references alive."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_tasks: set[asyncio.Task] = set()


# Start a coroutine in the background; the durable worker will replace this seam later.
def spawn(coro: Coroutine[Any, Any, None], name: str) -> None:
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)