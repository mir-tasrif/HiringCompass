"""Recruiter-owned F5 roster, batch results, transcript corrections, and review endpoints."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_interviewer
from app.db.models import F5Batch, F5Run, User
from app.db.session import get_session
from app.services.f5_analysis import correct_grouping, correct_transcript, owned_run, ready_roster, review_run, start_batch

router = APIRouter(prefix="/f5", tags=["f5-analysis"])


class StartBody(BaseModel):
    application_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class CorrectionBody(BaseModel):
    turn_id: str
    text: str = Field(min_length=1, max_length=10000)


class GroupingBody(BaseModel):
    segments: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class ReviewBody(BaseModel):
    decision: Literal["approve", "reanalyse", "reject_candidate"]
    reason: str = Field(default="", max_length=2000)


def _run_out(run: F5Run) -> dict[str, Any]:
    progress = (run.result or {}).get("_progress", {}) if run.status != "completed" else {}
    return {"id": str(run.id), "batch_id": str(run.batch_id), "application_id": str(run.application_id),
            "interview_id": str(run.interview_id), "job_version": run.job_version, "status": run.status,
            "review_status": run.review_status, "review_reason": run.review_reason,
            "result": run.result if run.status == "completed" else None,
            "progress": {"completed": len(progress.get("evaluations", [])),
                         "total": len(progress.get("segments", []))} if progress.get("segments") else None,
            "next_retry_at": run.next_retry_at.isoformat() if run.next_retry_at else None,
            "transcript": run.transcript, "segment_overrides": run.segment_overrides,
            "corrections": run.corrections,
            "error": run.error, "created_at": run.created_at.isoformat() if run.created_at else None}


@router.get("/ready")
async def ready(job_id: uuid.UUID | None = None, user: User = Depends(get_current_interviewer),
                session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    return await ready_roster(session, user.id, job_id)


@router.post("/batches", status_code=202)
async def create_batch(body: StartBody, user: User = Depends(get_current_interviewer),
                       session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    batch = await start_batch(session, user.id, body.application_ids)
    return {"id": str(batch.id), "status": batch.status}


@router.get("/batches/{batch_id}")
async def batch_detail(batch_id: uuid.UUID, user: User = Depends(get_current_interviewer),
                       session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    batch = await session.scalar(select(F5Batch).where(F5Batch.id == batch_id, F5Batch.owner_id == user.id))
    if batch is None:
        raise HTTPException(404, "F5 batch not found.")
    runs = list(await session.scalars(select(F5Run).where(F5Run.batch_id == batch.id).order_by(F5Run.created_at)))
    return {"id": str(batch.id), "status": batch.status, "runs": [_run_out(run) for run in runs]}


@router.get("/runs")
async def all_runs(user: User = Depends(get_current_interviewer),
                   session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    runs = list(await session.scalars(select(F5Run).join(F5Batch, F5Batch.id == F5Run.batch_id)
        .where(F5Batch.owner_id == user.id).order_by(F5Run.created_at.desc(), F5Run.id.desc()).limit(100)))
    return [_run_out(run) for run in runs]


@router.get("/applications/{application_id}/runs")
async def application_runs(application_id: uuid.UUID, user: User = Depends(get_current_interviewer),
                           session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    runs = list(await session.scalars(select(F5Run).join(F5Batch, F5Batch.id == F5Run.batch_id)
        .where(F5Run.application_id == application_id, F5Batch.owner_id == user.id)
        .order_by(F5Run.created_at.desc(), F5Run.id.desc())))
    return [_run_out(run) for run in runs]


@router.post("/runs/{run_id}/corrections")
async def correct(run_id: uuid.UUID, body: CorrectionBody, user: User = Depends(get_current_interviewer),
                  session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    run = await owned_run(session, run_id, user.id)
    await correct_transcript(session, run, body.turn_id, body.text, user.id)
    return _run_out(run)


@router.post("/runs/{run_id}/grouping")
async def regroup(run_id: uuid.UUID, body: GroupingBody, user: User = Depends(get_current_interviewer),
                  session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    run = await owned_run(session, run_id, user.id)
    await correct_grouping(session, run, body.segments, user.id)
    return _run_out(run)


@router.post("/runs/{run_id}/review")
async def decide(run_id: uuid.UUID, body: ReviewBody, user: User = Depends(get_current_interviewer),
                 session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    run = await owned_run(session, run_id, user.id)
    if body.decision == "reject_candidate" and not body.reason.strip():
        raise HTTPException(422, "Give a reason for rejecting the candidate.")
    await review_run(session, run, body.decision, body.reason, user.id)
    return _run_out(run)
