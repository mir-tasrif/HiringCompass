"""Record CV application stage changes and signal concurrent recruiter decisions.

The worker and candidate-review API share this event writer so stage history
always records the previous and next stage in the same database transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, ApplicationEvent


class CandidateStageChanged(Exception):
    """A recruiter moved the application while an AI task was running."""


async def record_stage_event(session: AsyncSession, application: Application, actor_id: uuid.UUID | None,
                             action: str, next_stage: str, reason: str) -> None:
    old_stage = application.stage
    application.stage = next_stage
    session.add(ApplicationEvent(application_id=application.id, actor_id=actor_id, action=action,
                                 from_stage=old_stage, to_stage=next_stage, reason=reason))
