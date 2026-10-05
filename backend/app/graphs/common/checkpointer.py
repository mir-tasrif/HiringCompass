"""Durable LangGraph checkpointer on PostgreSQL (WBS 2.2.2): graph state survives restarts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("graph.checkpointer")


# Open a connection pool and yield a ready AsyncPostgresSaver; the pool closes on exit.
# `setup=True` creates the checkpoint tables if they do not exist (idempotent).
@asynccontextmanager
async def open_checkpointer(conninfo: str | None = None, *, setup: bool = True, max_size: int = 10) -> AsyncIterator[AsyncPostgresSaver]:
    pool = AsyncConnectionPool(
        conninfo=conninfo or get_settings().checkpoint_db_url,
        max_size=max_size,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open(wait=True)
    try:
        saver = AsyncPostgresSaver(pool)
        if setup:
            await saver.setup()
        logger.info("checkpointer ready")
        yield saver
    finally:
        await pool.close()