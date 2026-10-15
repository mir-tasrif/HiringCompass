"""Save an F1 screening result and advance the candidate to recruiter review.

The F1 graph evaluates evidence; this module owns the guarded database write.
It checks the application stage after graph execution to avoid overwriting a
recruiter's concurrent decision.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, CandidateScreening
from app.services.cv_stage_events import CandidateStageChanged, record_stage_event


async def save_screening_result(session: AsyncSession, application: Application,
                                result: dict[str, Any]) -> CandidateScreening:
    current_application = await session.scalar(select(Application).where(Application.id == application.id)
        .with_for_update().execution_options(populate_existing=True))
    if current_application is None or current_application.stage != "ranking":
        raise CandidateStageChanged()
    screening = CandidateScreening(application_id=application.id, job_id=uuid.UUID(result["job_id"]),
        job_version=result["job_version"], suitability_score=result["suitability_score"],
        mandatory_pass=result["mandatory_pass"], requirements=result["requirements"],
        explanation=result["explanation"], prompt_key=result["prompt_key"],
        prompt_version=result["prompt_version"])
    session.add(screening)
    await record_stage_event(session, current_application, None, "screened", "f1_review",
        "All mandatory requirements were met or partially met; ranked and awaiting recruiter review." if result["mandatory_pass"] else
        "At least one mandatory requirement was unmet or could not be confirmed; screened out without a rank and awaiting recruiter review.")
    return screening
