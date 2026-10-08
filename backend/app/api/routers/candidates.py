"""Recruiter candidate pipeline, CV processing batches, F11/F1 review, and rejection history."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_interviewer
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.models import (Application, ApplicationEvent, AuditEvent, Candidate, CandidateProfile, CandidateScreening,
                           CvBatch, CvBatchItem, Document, Job, User)
from app.db.session import SessionLocal, get_session
from app.services.cv_tasks import _event, _finalize_batch, _recompute_job_ranks, refresh_batch_status

router = APIRouter(prefix="/candidates", tags=["candidates"])
_SECTIONS = {"uploaded", "integrity", "parsed", "ranking", "scoring", "interview", "assessment", "final", "review", "rejected"}


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
    converted_from_docx: bool
    size_bytes: int
    uploaded_at: datetime
    stage: str
    rejection_action: str | None = None
    rejection_reason: str | None = None
    rejected_from_stage: str | None = None
    rejected_at: datetime | None = None
    events: list[CandidateEventOut] = Field(default_factory=list)
    parsed_profile: dict | None = None
    integrity_verdict: str | None = None
    integrity_signals: list[dict] = Field(default_factory=list)
    extraction_method: str | None = None
    screening: dict | None = None
    integrity_task_id: uuid.UUID | None = None
    integrity_task_status: str | None = None
    integrity_task_error: str | None = None


class CandidateActionBody(BaseModel):
    application_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    action: Literal["delete", "reject", "permanent_delete"]
    reason: str = Field(min_length=1, max_length=2000)


class CandidateActionOut(BaseModel):
    rejected_ids: list[uuid.UUID]


class StartBatchBody(BaseModel):
    application_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class BatchItemOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    status: str
    attempts: int
    error: str | None


class BatchOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    kind: str
    status: str
    total: int
    completed: int
    failed: int
    waiting_review: int
    rejected: int
    items: list[BatchItemOut]


class StartBatchesOut(BaseModel):
    batches: list[BatchOut]


class RetryOut(BaseModel):
    id: uuid.UUID
    status: str


class ReviewDecisionBody(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=2000)


class ReviewDecisionOut(BaseModel):
    application_id: uuid.UUID
    stage: str
    decision: str


def _batch_out(batch: CvBatch, items: list[CvBatchItem]) -> BatchOut:
    return BatchOut(id=batch.id, created_at=batch.created_at, kind=batch.kind, status=batch.status, total=len(items),
        completed=sum(item.status == "completed" for item in items),
        failed=sum(item.status == "failed" for item in items),
        waiting_review=sum(item.status == "waiting_review" for item in items),
        rejected=sum(item.status == "rejected" for item in items),
        items=[BatchItemOut(id=item.id, application_id=item.application_id, status=item.status,
                            attempts=item.attempts, error=item.error) for item in items])


@router.get("", response_model=list[CandidateCvOut])
async def list_candidate_cvs(
    section: str = "uploaded",
    user: User = Depends(get_current_interviewer),
    session: AsyncSession = Depends(get_session),
) -> list[CandidateCvOut]:
    if section not in _SECTIONS:
        raise HTTPException(status_code=422, detail="Unknown candidate pipeline section.")
    statement = (select(Application, Candidate, Document, Job, CandidateProfile, CandidateScreening)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Document, Document.application_id == Application.id)
        .join(Job, Job.id == Application.job_id)
        .outerjoin(CandidateProfile, CandidateProfile.application_id == Application.id)
        .outerjoin(CandidateScreening, CandidateScreening.application_id == Application.id)
        .where(Job.owner_id == user.id, Job.active_version.is_not(None)))
    if section == "uploaded":
        statement = statement.where(Application.stage == "uploaded")
    elif section == "integrity":
        statement = statement.where(or_(
            Application.stage == "integrity_check",
            CandidateProfile.integrity_verdict.is_not(None),
            and_(Application.stage == "rejected", Application.rejected_from_stage == "integrity_check"),
        ))
    elif section == "parsed":
        statement = statement.where(Application.stage == "parsed")
    elif section == "ranking":
        statement = statement.where(CandidateScreening.id.is_not(None), CandidateScreening.mandatory_pass.is_(True))
    elif section == "scoring":
        statement = statement.where(Application.stage == "scoring")
    elif section == "review":
        statement = statement.where(Application.stage.in_(["integrity_review", "f1_review"]))
    elif section == "rejected":
        statement = statement.where(Application.stage == "rejected")
    else:
        target = {"interview": "interview_selection", "assessment": "assessed", "final": "final_selection"}[section]
        statement = statement.where(Application.stage == target)
    if section == "ranking":
        statement = statement.order_by(Job.public_code.asc(), CandidateScreening.rank.asc().nulls_last(),
                                       CandidateScreening.suitability_score.desc(), Application.created_at.desc())
    else:
        statement = statement.order_by(Application.created_at.desc(), Application.id)
    rows = (await session.execute(statement)).all()
    if not rows:
        return []
    ids = [application.id for application, *_ in rows]
    event_rows = (await session.execute(select(ApplicationEvent).where(ApplicationEvent.application_id.in_(ids))
                    .order_by(ApplicationEvent.created_at.desc(), ApplicationEvent.id))).scalars().all()
    task_rows = (await session.execute(select(CvBatchItem).join(CvBatch, CvBatch.id == CvBatchItem.batch_id)
                    .where(CvBatchItem.application_id.in_(ids), CvBatch.kind == "integrity_parse")
                    .order_by(CvBatchItem.created_at.desc(), CvBatchItem.id))).scalars().all()
    integrity_tasks: dict[uuid.UUID, CvBatchItem] = {}
    for task in task_rows:
        integrity_tasks.setdefault(task.application_id, task)
    events: dict[uuid.UUID, list[CandidateEventOut]] = {}
    for event in event_rows:
        events.setdefault(event.application_id, []).append(CandidateEventOut(action=event.action,
            from_stage=event.from_stage, to_stage=event.to_stage, reason=event.reason, created_at=event.created_at))
    result = []
    for application, candidate, document, job, parsed, screening in rows:
        screening_data = None if screening is None else {
            "suitability_score": screening.suitability_score, "mandatory_pass": screening.mandatory_pass,
            "requirements": screening.requirements, "explanation": screening.explanation, "rank": screening.rank,
        }
        result.append(CandidateCvOut(id=application.id, job_id=job.id, job_code=job.public_code or "",
            job_title=job.title, job_version=application.job_version, candidate_number=application.candidate_number,
            candidate_name=candidate.full_name, source=application.source, original_filename=document.original_filename,
            converted_from_docx=document.status == "converted_from_docx", size_bytes=document.size_bytes,
            uploaded_at=document.created_at, stage=application.stage, rejection_action=application.rejection_action,
            rejection_reason=application.rejection_reason, rejected_from_stage=application.rejected_from_stage,
            rejected_at=application.rejected_at, events=events.get(application.id, []),
            parsed_profile=parsed.profile if parsed and parsed.profile else None,
            integrity_verdict=parsed.integrity_verdict if parsed else None,
            integrity_signals=parsed.integrity_signals if parsed else [],
            extraction_method=parsed.extraction_method if parsed else None, screening=screening_data,
            integrity_task_id=integrity_tasks[application.id].id if application.id in integrity_tasks else None,
            integrity_task_status=integrity_tasks[application.id].status if application.id in integrity_tasks else None,
            integrity_task_error=integrity_tasks[application.id].error if application.id in integrity_tasks else None))
    return result


async def _start_batches(session: AsyncSession, user: User, body: StartBatchBody, kind: str) -> StartBatchesOut:
    ids = list(dict.fromkeys(body.application_ids))
    rows = (await session.execute(select(Application, Job).join(Job, Job.id == Application.job_id)
        .where(Application.id.in_(ids), Job.owner_id == user.id, Job.archived_at.is_(None)).with_for_update())).all()
    if len(rows) != len(ids):
        raise NotFoundError("One or more selected CVs are unavailable in active jobs.")
    expected_stage = "uploaded" if kind == "integrity_parse" else "parsed"
    if any(application.stage != expected_stage for application, _ in rows):
        raise HTTPException(status_code=409, detail=f"All selected CVs must be in the {expected_stage} stage.")
    grouped: dict[uuid.UUID, list[Application]] = {}
    for application, job in rows:
        grouped.setdefault(job.id, []).append(application)
    batches: list[BatchOut] = []
    for job_id, applications in grouped.items():
        batch = CvBatch(owner_id=user.id, job_id=job_id, kind=kind, status="queued", total=len(applications))
        session.add(batch)
        await session.flush()
        items = []
        for application in applications:
            item = CvBatchItem(batch_id=batch.id, application_id=application.id, status="queued")
            session.add(item)
            items.append(item)
            await _event(session, application, user.id, "batch_started", "integrity_check" if kind == "integrity_parse" else "ranking",
                         "Recruiter started CV integrity and parsing." if kind == "integrity_parse" else "Recruiter started F1 requirement-based ranking.")
        await session.flush()
        batches.append(_batch_out(batch, items))
    await session.commit()
    return StartBatchesOut(batches=batches)


@router.post("/batches/integrity", response_model=StartBatchesOut, status_code=202)
async def start_integrity_batch(body: StartBatchBody, user: User = Depends(get_current_interviewer),
                                session: AsyncSession = Depends(get_session)) -> StartBatchesOut:
    return await _start_batches(session, user, body, "integrity_parse")


@router.post("/batches/ranking", response_model=StartBatchesOut, status_code=202)
async def start_ranking_batch(body: StartBatchBody, user: User = Depends(get_current_interviewer),
                              session: AsyncSession = Depends(get_session)) -> StartBatchesOut:
    return await _start_batches(session, user, body, "ranking")


@router.get("/batches", response_model=list[BatchOut])
async def list_active_batches(user: User = Depends(get_current_interviewer),
                             session: AsyncSession = Depends(get_session)) -> list[BatchOut]:
    batches = list(await session.scalars(select(CvBatch).where(CvBatch.owner_id == user.id)
        .order_by(CvBatch.created_at.desc()).limit(20)))
    output = []
    for batch in batches:
        items = list(await session.scalars(select(CvBatchItem).where(CvBatchItem.batch_id == batch.id)
                                           .order_by(CvBatchItem.created_at)))
        output.append(_batch_out(batch, items))
    return output


@router.get("/batches/{batch_id}", response_model=BatchOut)
async def get_batch(batch_id: uuid.UUID, user: User = Depends(get_current_interviewer),
                    session: AsyncSession = Depends(get_session)) -> BatchOut:
    batch = await session.scalar(select(CvBatch).where(CvBatch.id == batch_id, CvBatch.owner_id == user.id))
    if batch is None:
        raise NotFoundError("CV batch not found.")
    items = list(await session.scalars(select(CvBatchItem).where(CvBatchItem.batch_id == batch.id).order_by(CvBatchItem.created_at)))
    return _batch_out(batch, items)


@router.post("/batch-items/{item_id}/retry", response_model=RetryOut, status_code=202)
async def retry_batch_item(item_id: uuid.UUID, user: User = Depends(get_current_interviewer),
                           session: AsyncSession = Depends(get_session)) -> RetryOut:
    item = await session.scalar(select(CvBatchItem).join(CvBatch, CvBatch.id == CvBatchItem.batch_id)
        .where(CvBatchItem.id == item_id, CvBatch.owner_id == user.id).with_for_update())
    if item is None:
        raise NotFoundError("CV processing task not found.")
    if item.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed CV tasks can be retried.")
    application = await session.get(Application, item.application_id)
    if application is None or application.stage == "rejected":
        raise HTTPException(status_code=409, detail="Rejected CVs must be uploaded again to retry.")
    item.status, item.attempts, item.error, item.lease_expires_at = "queued", 0, None, None
    batch = await session.get(CvBatch, item.batch_id)
    if batch:
        batch.status, batch.completion_notified = "queued", False
    await session.commit()
    return RetryOut(id=item.id, status=item.status)


@router.post("/{application_id}/review", response_model=ReviewDecisionOut)
async def decide_candidate_review(application_id: uuid.UUID, body: ReviewDecisionBody,
                                  user: User = Depends(get_current_interviewer),
                                  session: AsyncSession = Depends(get_session)) -> ReviewDecisionOut:
    row = (await session.execute(select(Application, CandidateProfile, CandidateScreening)
        .join(Job, Job.id == Application.job_id)
        .outerjoin(CandidateProfile, CandidateProfile.application_id == Application.id)
        .outerjoin(CandidateScreening, CandidateScreening.application_id == Application.id)
        .where(Application.id == application_id, Job.owner_id == user.id).with_for_update(of=Application))).first()
    if row is None:
        raise NotFoundError("Candidate review not found.")
    application, profile, screening = row
    reason = body.reason.strip()
    if body.decision == "reject" and not reason:
        raise HTTPException(status_code=422, detail="A reason is required when rejecting a CV.")
    actor_reason = reason or "Recruiter approved after reviewing the recorded evidence."
    finalize_batch_id: uuid.UUID | None = None
    if application.stage == "integrity_review":
        if profile is None:
            raise HTTPException(status_code=409, detail="Integrity evidence is not available for this CV.")
        if body.decision == "approve":
            profile.integrity_verdict = "approved_after_review"
            await _event(session, application, user.id, "integrity_approved", "integrity_check", actor_reason)
            item = await session.scalar(select(CvBatchItem).where(CvBatchItem.application_id == application.id,
                CvBatchItem.status == "waiting_review").order_by(CvBatchItem.created_at.desc()).with_for_update())
            if item is None:
                raise HTTPException(status_code=409, detail="The original CV processing task cannot be resumed.")
            item.status, item.error, item.attempts = "queued", None, 0
            await refresh_batch_status(session, item.batch_id)
            next_stage = "integrity_check"
        else:
            evidence = "; ".join(signal.get("evidence", "") for signal in (profile.integrity_signals or []))
            final_reason = f"Integrity review rejected. Evidence: {evidence}. Recruiter reason: {reason}"[:4000]
            await _event(session, application, user.id, "rejected", "rejected", final_reason)
            application.rejection_action, application.rejection_reason = "rejected", final_reason
            application.rejected_from_stage, application.rejected_at, application.rejected_by = "integrity_review", datetime.now(timezone.utc), user.id
            item = await session.scalar(select(CvBatchItem).where(CvBatchItem.application_id == application.id,
                CvBatchItem.status == "waiting_review").order_by(CvBatchItem.created_at.desc()).with_for_update())
            if item:
                item.status, item.error = "rejected", final_reason
                await refresh_batch_status(session, item.batch_id)
                finalize_batch_id = item.batch_id
            next_stage = "rejected"
    elif application.stage == "f1_review":
        if screening is None:
            raise HTTPException(status_code=409, detail="F1 screening results are missing.")
        if body.decision == "approve":
            if not screening.mandatory_pass:
                raise HTTPException(status_code=409, detail="The mandatory-requirement gate screened out this CV. It cannot proceed to Feature 2.")
            await _event(session, application, user.id, "f1_approved", "scoring", actor_reason)
            next_stage = "scoring"
        else:
            await _event(session, application, user.id, "rejected", "rejected", reason)
            application.rejection_action, application.rejection_reason = "rejected", reason
            application.rejected_from_stage, application.rejected_at, application.rejected_by = "f1_review", datetime.now(timezone.utc), user.id
            next_stage = "rejected"
    else:
        raise HTTPException(status_code=409, detail="This CV is not waiting for a recruiter decision.")
    await session.commit()
    if finalize_batch_id:
        await _finalize_batch(SessionLocal, finalize_batch_id)
    if screening is not None and next_stage == "rejected":
        await _recompute_job_ranks(session, application.job_id)
        await session.commit()
    return ReviewDecisionOut(application_id=application.id, stage=next_stage, decision=body.decision)


@router.post("/actions", response_model=CandidateActionOut)
async def act_on_candidate_cvs(body: CandidateActionBody, user: User = Depends(get_current_interviewer),
                               session: AsyncSession = Depends(get_session)) -> CandidateActionOut:
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A reason is required.")
    ids = list(dict.fromkeys(body.application_ids))
    rows = (await session.execute(select(Application).join(Job, Job.id == Application.job_id)
        .where(Job.owner_id == user.id, Application.id.in_(ids)).with_for_update())).scalars().all()
    if len(rows) != len(ids):
        raise NotFoundError("One or more CVs were not found.")
    if body.action == "permanent_delete":
        if any(application.stage != "rejected" for application in rows):
            raise HTTPException(status_code=409, detail="Only CVs in Rejected can be permanently deleted.")
        documents = list((await session.scalars(select(Document).where(Document.application_id.in_(ids)))).all())
        file_paths = {document.file_path for document in documents}
        candidate_ids = {application.candidate_id for application in rows}
        batch_ids = set((await session.scalars(select(CvBatchItem.batch_id)
            .where(CvBatchItem.application_id.in_(ids)))).all())
        for application in rows:
            session.add(AuditEvent(actor_id=user.id, action="candidate_cv_permanently_deleted",
                entity_type="application", entity_id=str(application.id), detail={"job_id": str(application.job_id)}))
            await session.delete(application)
        await session.flush()
        for candidate_id in candidate_ids:
            still_applied = await session.scalar(select(Application.id)
                .where(Application.candidate_id == candidate_id).limit(1))
            if still_applied is None:
                candidate = await session.get(Candidate, candidate_id)
                if candidate:
                    await session.delete(candidate)
        for batch_id in batch_ids:
            has_items = await session.scalar(select(CvBatchItem.id).where(CvBatchItem.batch_id == batch_id).limit(1))
            if has_items is None:
                batch = await session.get(CvBatch, batch_id)
                if batch:
                    await session.delete(batch)
        await session.commit()

        storage_root = get_settings().file_storage_dir.resolve()
        remaining_paths = set((await session.scalars(select(Document.file_path)
            .where(Document.file_path.in_(file_paths)))).all()) if file_paths else set()
        for relative_path in file_paths - remaining_paths:
            path = (storage_root / relative_path).resolve()
            try:
                path.relative_to(storage_root)
            except ValueError:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return CandidateActionOut(rejected_ids=ids)
    if any(application.stage == "rejected" for application in rows):
        raise HTTPException(status_code=409, detail="A selected CV is already in Rejected.")
    now = datetime.now(timezone.utc)
    action_name = "deleted" if body.action == "delete" else "rejected"
    for application in rows:
        previous_stage = application.stage
        application.stage, application.rejection_action, application.rejection_reason = "rejected", action_name, reason
        application.rejected_from_stage, application.rejected_at, application.rejected_by = previous_stage, now, user.id
        session.add(ApplicationEvent(application_id=application.id, actor_id=user.id, action=action_name,
            from_stage=previous_stage, to_stage="rejected", reason=reason))
    pending_items = list((await session.scalars(select(CvBatchItem).where(CvBatchItem.application_id.in_(ids),
        CvBatchItem.status.in_(["queued", "processing", "waiting_review", "failed"])).with_for_update())).all())
    finalize_ids: set[uuid.UUID] = set()
    for item in pending_items:
        item.status, item.error = "rejected", reason
        finalize_ids.add(item.batch_id)
        await refresh_batch_status(session, item.batch_id)
    await session.commit()
    for batch_id in finalize_ids:
        await _finalize_batch(SessionLocal, batch_id)
    return CandidateActionOut(rejected_ids=[application.id for application in rows])

