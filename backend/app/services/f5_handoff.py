"""Resolve an F5 handoff to the latest usable interview for an application."""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InterviewSession, InterviewTurn


async def latest_transcript_session(session: AsyncSession, application_id: uuid.UUID) -> InterviewSession | None:
    has_turns = exists(select(InterviewTurn.id).where(InterviewTurn.session_id == InterviewSession.id))
    return await session.scalar(
        select(InterviewSession).where(
            InterviewSession.application_id == application_id,
            InterviewSession.status == "ended",
            InterviewSession.ended_at.is_not(None),
            has_turns,
        ).order_by(InterviewSession.ended_at.desc(), InterviewSession.id.desc()).limit(1)
    )
