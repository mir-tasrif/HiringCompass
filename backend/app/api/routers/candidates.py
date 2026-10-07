"""Recruiter views and actions for uploaded and rejected candidate CVs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_interviewer
from app.core.errors import NotFoundError
from app.db.models import Application, ApplicationEvent, Candidate, Document, Job, User
from app.db.session import get_session

router = APIRouter(prefix="/candidates", tags=["candidates"])


class CandidateEventOut(BaseModel):
    action: str
    from_stage: str
    to_stage: str
    reason: str
    created_at: datetime


class CandidateCvOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    job_code: str
    job_title: str
    job_version: int
    candidate_number: int
    candidate_name: str
    source: str
    original_filename: str | None
    size_bytes: int
    uploaded_at: datetime
    stage: str
    rejection_action: str | None = None
    rejection_reason: str | None = None
    rejected_from_stage: str | None = None
    rejected_at: datetime | None = None
    events: list[CandidateEventOut] = Field(default_factory=list)


class CandidateActionBody(BaseModel):
    application_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    action: Literal["delete", "reject"]
    reason: str = Field(min_length=1, max_length=2000)


class CandidateActionOut(BaseModel):
    rejected_ids: list[uuid.UUID]


@router.get("", response_model=list[CandidateCvOut])
async def list_candidate_cvs(
    section: Literal["uploaded", "rejected"] = "uploaded",
    user: User = Depends(get_current_interviewer),
    session: AsyncSession = Depends(get_session),
) -> list[CandidateCvOut]:
    target_stage = "rejected" if section == "rejected" else "uploaded"
    rows = (await session.execute(
        select(Application, Candidate, Document, Job)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Document, Document.application_id == Application.id)
        .join(Job, Job.id == Application.job_id)
        .where(Job.owner_id == user.id, Job.active_version.is_not(None), Application.stage == target_stage)
        .order_by(Application.created_at.desc(), Application.id)
    )).all()
    if not rows:
        return []
    application_ids = [application.id for application, _, _, _ in rows]
    event_rows = (await session.execute(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id.in_(application_ids))
        .order_by(ApplicationEvent.created_at.desc(), ApplicationEvent.id)
    )).scalars().all()
    events_by_application: dict[uuid.UUID, list[CandidateEventOut]] = {}
    for event in event_rows:
        events_by_application.setdefault(event.application_id, []).append(CandidateEventOut(
            action=event.action, from_stage=event.from_stage, to_stage=event.to_stage,
            reason=event.reason, created_at=event.created_at,
        ))
    return [CandidateCvOut(
        id=application.id, job_id=job.id, job_code=job.public_code or "", job_title=job.title,
        job_version=application.job_version, candidate_number=application.candidate_number,
        candidate_name=candidate.full_name, source=application.source,
        original_filename=document.original_filename, size_bytes=document.size_bytes,
        uploaded_at=document.created_at, stage=application.stage,
        rejection_action=application.rejection_action, rejection_reason=application.rejection_reason,
        rejected_from_stage=application.rejected_from_stage, rejected_at=application.rejected_at,
        events=events_by_application.get(application.id, []),
    ) for application, candidate, document, job in rows]


@router.post("/actions", response_model=CandidateActionOut)
async def act_on_candidate_cvs(
    body: CandidateActionBody,
    user: User = Depends(get_current_interviewer),
    session: AsyncSession = Depends(get_session),
) -> CandidateActionOut:
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A reason is required.")
    ids = list(dict.fromkeys(body.application_ids))
    rows = (await session.execute(
        select(Application)
        .join(Job, Job.id == Application.job_id)
        .where(Job.owner_id == user.id, Application.id.in_(ids))
        .with_for_update()
    )).scalars().all()
    if len(rows) != len(ids):
        raise NotFoundError("One or more CVs were not found.")
    if any(application.stage == "rejected" for application in rows):
        raise HTTPException(status_code=409, detail="A selected CV is already in Rejected.")

    now = datetime.now().astimezone()
    action_name = "deleted" if body.action == "delete" else "rejected"
    for application in rows:
        previous_stage = application.stage
        application.stage = "rejected"
        application.rejection_action = action_name
        application.rejection_reason = reason
        application.rejected_from_stage = previous_stage
        application.rejected_at = now
        application.rejected_by = user.id
        session.add(ApplicationEvent(
            application_id=application.id, actor_id=user.id, action=action_name,
            from_stage=previous_stage, to_stage="rejected", reason=reason,
        ))
    await session.commit()
    return CandidateActionOut(rejected_ids=[application.id for application in rows])
