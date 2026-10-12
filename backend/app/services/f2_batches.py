"""Shared F2 batch start for the Candidates page and job chat."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, CvBatch, CvBatchItem, Job
from app.services.cv_tasks import _event
from app.services.f2_policy import choose_policy, validate_weights


async def start_f2_batch(session: AsyncSession, *, owner_id: uuid.UUID, application_ids: list[uuid.UUID],
                         weights: dict[str, int], thread_id: uuid.UUID | None = None,
                         rescore: bool = False) -> CvBatch:
    ids = list(dict.fromkeys(application_ids))
    if not ids or len(ids) > 20:
        raise HTTPException(status_code=422, detail="Select between 1 and 20 CVs.")
    try:
        validate_weights(weights)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = (await session.execute(select(Application, Job).join(Job, Job.id == Application.job_id)
        .where(Application.id.in_(ids), Job.owner_id == owner_id, Job.archived_at.is_(None),
               Job.active_version.is_not(None)).with_for_update(of=Application))).all()
    if len(rows) != len(ids) or len({job.id for _, job in rows}) != 1:
        raise HTTPException(status_code=409, detail="Select F2-ready CVs from one active job.")
    job = rows[0][1]
    expected_stage = "f2_review" if rescore else "scoring"
    if any(application.stage != expected_stage for application, _ in rows):
        raise HTTPException(status_code=409, detail=f"Only candidates in {expected_stage} may start this F2 batch.")
    await session.scalar(select(Job).where(Job.id == job.id).with_for_update())
    policy = await choose_policy(session, job, owner_id, weights)
    batch = CvBatch(owner_id=owner_id, job_id=job.id, thread_id=thread_id,
                    scoring_policy_id=policy.id, kind="scoring", status="queued", total=len(rows))
    session.add(batch)
    await session.flush()
    for application, _ in rows:
        session.add(CvBatchItem(batch_id=batch.id, application_id=application.id, status="queued"))
        await _event(session, application, owner_id, "batch_started", "scoring_processing",
                     f"Recruiter {'restarted' if rescore else 'started'} F2 scoring with policy version {policy.version}.")
    await session.commit()
    return batch
