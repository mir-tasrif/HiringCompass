"""Async database engine, session factory and health probe."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

# Shared async engine (psycopg 3); pre-ping drops dead connections after a DB restart.
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)

# Session factory; objects stay usable after commit.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# FastAPI dependency yielding one session per request.
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


# Return True when the database answers a trivial query.
async def check_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False