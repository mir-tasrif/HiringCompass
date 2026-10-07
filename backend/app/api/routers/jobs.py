"""Approved job management and recruiter-managed CV submissions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_interviewer
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError, UploadRejected
from app.db.models import Application, ApplicationEvent, Candidate, Document, Job, JobPosting, JobVersion, User
from app.db.session import get_session
from app.services import uploads
from app.services.chat import ChatRuntime
from app.services.postings import post_to_discord

router = APIRouter(prefix="/jobs", tags=["jobs"])
settings = get_settings()


class JobCardOut(BaseModel):
    id: uuid.UUID
    public_code: str
    title: str
    version: int
    summary: str
    location: str | None
    employment_type: str | None
    jd_text: str
    updated_at: datetime
    posted_platforms: list[str]


class JobOut(JobCardOut):
    profile: dict[str, Any]


class ApplicationOut(BaseModel):
    id: uuid.UUID
    candidate_number: int
    candidate_name: str
    original_filename: str | None
    size_bytes: int
    status: str
    uploaded_at: datetime


class UploadOut(BaseModel):
    filename: str
    success: bool
    application_id: uuid.UUID | None = None
    candidate_number: int | None = None
    candidate_name: str | None = None
    error: str | None = None


class DeleteApplicationsBody(BaseModel):
    application_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)


class DeleteApplicationsOut(BaseModel):
    rejected_ids: list[uuid.UUID]


class PostOut(BaseModel):
    status: str
    platform: str = "discord"
    channel: str
    version: int


async def _approved_job(session: AsyncSession, job_id: uuid.UUID, owner_id: uuid.UUID) -> tuple[Job, JobVersion]:
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id))
    if job is None or job.active_version is None:
        raise NotFoundError("Approved job not found.")
    version = await session.scalar(select(JobVersion).where(
        JobVersion.job_id == job.id, JobVersion.version == job.active_version, JobVersion.status == "active"
    ))
    if version is None:
        raise NotFoundError("Approved job not found.")
    if job.public_code is None:
        raise NotFoundError("Approved job code is not available.")
    return job, version


async def _job_out(session: AsyncSession, job: Job, version: JobVersion) -> JobOut:
    posted = list(await session.scalars(select(JobPosting.platform).where(
        JobPosting.job_id == job.id, JobPosting.version == version.version, JobPosting.status == "posted"
    )))
    profile = version.profile
    return JobOut(
        id=job.id, public_code=job.public_code, title=job.title, version=version.version,
        summary=profile.get("summary", ""), location=profile.get("location"),
        employment_type=profile.get("employment_type"), jd_text=profile.get("posting_text", ""),
        updated_at=job.updated_at, posted_platforms=posted, profile=profile,
    )


@router.get("", response_model=list[JobCardOut])
async def list_approved_jobs(user: User = Depends(get_current_interviewer), session: AsyncSession = Depends(get_session)) -> list[JobCardOut]:
    rows = (await session.execute(
        select(Job, JobVersion)
        .join(JobVersion, (JobVersion.job_id == Job.id) & (JobVersion.version == Job.active_version))
        .where(Job.owner_id == user.id, Job.active_version.is_not(None), JobVersion.status == "active")
        .order_by(Job.created_at.desc(), Job.id)
    )).all()
    output = []
    for job, version in rows:
        item = await _job_out(session, job, version)
        output.append(JobCardOut(**item.model_dump(exclude={"profile"})))
    return output


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, user: User = Depends(get_current_interviewer), session: AsyncSession = Depends(get_session)) -> JobOut:
    job, version = await _approved_job(session, job_id, user.id)
    return await _job_out(session, job, version)


@router.post("/{job_id}/post", response_model=PostOut)
async def post_job(job_id: uuid.UUID, request: Request, user: User = Depends(get_current_interviewer),
                   session: AsyncSession = Depends(get_session)) -> PostOut:
    job, _ = await _approved_job(session, job_id, user.id)
    runtime: ChatRuntime = request.app.state.chat_runtime
    try:
        version, already_posted = await post_to_discord(session, runtime.deps.settings, job.id)
    except AppError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PostOut(status="already_posted" if already_posted else "posted", channel=runtime.deps.settings.discord_channel_name, version=version)


@router.get("/{job_id}/applications", response_model=list[ApplicationOut])
async def list_applications(job_id: uuid.UUID, user: User = Depends(get_current_interviewer),
                            session: AsyncSession = Depends(get_session)) -> list[ApplicationOut]:
    job, _ = await _approved_job(session, job_id, user.id)
    rows = (await session.execute(
        select(Application, Candidate, Document)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Document, Document.application_id == Application.id)
        .where(Application.job_id == job.id, Application.stage == "uploaded")
        .order_by(Application.candidate_number)
    )).all()
    return [ApplicationOut(
        id=application.id, candidate_number=application.candidate_number, candidate_name=candidate.full_name,
        original_filename=document.original_filename, size_bytes=document.size_bytes,
        status=document.status, uploaded_at=document.created_at,
    ) for application, candidate, document in rows]


@router.post("/{job_id}/applications", response_model=list[UploadOut])
async def upload_applications(job_id: uuid.UUID, files: Annotated[list[UploadFile], File()],
                              user: User = Depends(get_current_interviewer), session: AsyncSession = Depends(get_session)) -> list[UploadOut]:
    job, version = await _approved_job(session, job_id, user.id)
    output: list[UploadOut] = []
    for file in files:
        original_name = (file.filename or "upload.pdf").replace("\\", "/").rsplit("/", 1)[-1][:255]
        stored_path: str | None = None
        try:
            validated = await uploads.process_upload(file, settings)
            locked_job = await session.scalar(select(Job).where(Job.id == job.id).with_for_update())
            if locked_job is None or locked_job.active_version != version.version:
                raise NotFoundError("Approved job not found.")
            await uploads.ensure_not_duplicate(session, job.id, validated.content_hash)
            candidate_number = locked_job.next_candidate_number
            candidate_name = f"candidate{candidate_number:03d}"
            stored_path = uploads.store_upload(validated, settings)
            candidate = Candidate(full_name=candidate_name)
            session.add(candidate)
            await session.flush()
            application = Application(candidate_id=candidate.id, job_id=job.id, job_version=version.version,
                                      candidate_number=candidate_number, stage="uploaded")
            session.add(application)
            await session.flush()
            session.add(Document(application_id=application.id, version=1, file_path=stored_path,
                                 original_filename=original_name, content_hash=validated.content_hash,
                                 size_bytes=validated.size_bytes, status="stored"))
            locked_job.next_candidate_number += 1
            await session.commit()
            output.append(UploadOut(filename=original_name, success=True, application_id=application.id,
                                    candidate_number=candidate_number, candidate_name=candidate_name))
        except UploadRejected as exc:
            await session.rollback()
            output.append(UploadOut(filename=original_name, success=False, error=exc.message))
        except Exception:
            await session.rollback()
            if stored_path:
                (settings.file_storage_dir / stored_path).unlink(missing_ok=True)
            raise
        finally:
            await file.close()
    return output


@router.get("/{job_id}/applications/{application_id}/file")
async def preview_application_file(job_id: uuid.UUID, application_id: uuid.UUID,
                                   user: User = Depends(get_current_interviewer), session: AsyncSession = Depends(get_session)) -> FileResponse:
    job, _ = await _approved_job(session, job_id, user.id)
    row = (await session.execute(
        select(Application, Candidate, Document)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Document, Document.application_id == Application.id)
        .where(Application.id == application_id, Application.job_id == job.id)
    )).first()
    if row is None:
        raise NotFoundError("CV submission not found.")
    _, candidate, document = row
    root = settings.file_storage_dir.resolve()
    path = (root / document.file_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise NotFoundError("CV file not found.") from exc
    if not path.is_file():
        raise NotFoundError("CV file not found.")
    filename = document.original_filename or f"{candidate.full_name}.pdf"
    headers = {"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"}
    return FileResponse(path, media_type="application/pdf", headers=headers)


@router.delete("/{job_id}/applications", response_model=DeleteApplicationsOut)
async def delete_applications(job_id: uuid.UUID, body: DeleteApplicationsBody,
                              user: User = Depends(get_current_interviewer), session: AsyncSession = Depends(get_session)) -> DeleteApplicationsOut:
    job, _ = await _approved_job(session, job_id, user.id)
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A reason is required.")
    ids = list(dict.fromkeys(body.application_ids))
    rows = (await session.execute(
        select(Application).where(Application.job_id == job.id, Application.id.in_(ids)).with_for_update()
    )).scalars().all()
    if len(rows) != len(ids):
        raise NotFoundError("One or more CV submissions were not found.")
    if any(application.stage == "rejected" for application in rows):
        raise HTTPException(status_code=409, detail="A selected CV is already in Rejected.")
    now = datetime.now(timezone.utc)
    for application in rows:
        previous_stage = application.stage
        application.stage = "rejected"
        application.rejection_action = "deleted"
        application.rejection_reason = reason
        application.rejected_from_stage = previous_stage
        application.rejected_at = now
        application.rejected_by = user.id
        session.add(ApplicationEvent(
            application_id=application.id, actor_id=user.id, action="deleted",
            from_stage=previous_stage, to_stage="rejected", reason=reason,
        ))
    await session.commit()
    return DeleteApplicationsOut(rejected_ids=[application.id for application in rows])
