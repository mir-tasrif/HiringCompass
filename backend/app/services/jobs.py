"""Job and job-version persistence: drafts, immutable activation, derived posting text."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermanentError
from app.db.models import Job, JobVersion


# Create a job owned by an interviewer.
async def create_job(session: AsyncSession, owner_id: uuid.UUID, title: str) -> Job:
    job = Job(title=title, owner_id=owner_id)
    session.add(job)
    await session.commit()
    return job


# Fetch a job by id (None when missing).
async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await session.get(Job, job_id)


# Next unused version number for a job (1 for a new job).
async def next_version(session: AsyncSession, job_id: uuid.UUID) -> int:
    latest = await session.scalar(select(func.max(JobVersion.version)).where(JobVersion.job_id == job_id))
    return (latest or 0) + 1


# Fetch one version row (None when missing).
async def get_version(session: AsyncSession, job_id: uuid.UUID, version: int) -> JobVersion | None:
    return await session.scalar(select(JobVersion).where(JobVersion.job_id == job_id, JobVersion.version == version))


# Insert or update a DRAFT version; approved versions are immutable.
async def save_draft(session: AsyncSession, job_id: uuid.UUID, version: int, profile: dict[str, Any], rubric: dict[str, Any]) -> JobVersion:
    row = await get_version(session, job_id, version)
    if row and row.status != "draft":
        raise PermanentError("Only draft versions can be changed.")
    if row:
        row.profile, row.rubric = profile, rubric
    else:
        row = JobVersion(job_id=job_id, version=version, status="draft", profile=profile, rubric=rubric)
        session.add(row)
    await session.commit()
    return row


# Set a version's status (e.g. discarded, superseded).
async def set_status(session: AsyncSession, job_id: uuid.UUID, version: int, status: str) -> None:
    row = await get_version(session, job_id, version)
    if row is None:
        raise PermanentError("Job version not found.")
    row.status = status
    await session.commit()


# Activate a draft: previous active version becomes superseded and the job points at the new one. Idempotent.
async def activate_version(session: AsyncSession, job_id: uuid.UUID, version: int) -> None:
    row = await get_version(session, job_id, version)
    if row is None:
        raise PermanentError("Job version not found.")
    if row.status == "active":
        return
    if row.status != "draft":
        raise PermanentError("Only a draft version can be activated.")
    for previous in (await session.scalars(select(JobVersion).where(JobVersion.job_id == job_id, JobVersion.status == "active"))):
        previous.status = "superseded"
    row.status = "active"
    job = await session.get(Job, job_id)
    job.active_version = version
    await session.commit()


# Store the copyable posting text inside the version's profile (derived data; requirements and rubric stay untouched).
async def set_posting_text(session: AsyncSession, job_id: uuid.UUID, version: int, text: str) -> None:
    row = await get_version(session, job_id, version)
    if row is None:
        raise PermanentError("Job version not found.")
    row.profile = {**row.profile, "posting_text": text}
    await session.commit()