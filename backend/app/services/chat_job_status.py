"""Summarize an approved job's posting and CV pipeline state for recruiter chat."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, ChatThread, Job, JobPosting


async def job_activity_context(session: AsyncSession, thread: ChatThread,
                               posting_configured: bool) -> list[dict[str, Any]]:
    linked_job = None
    if thread.job_id:
        linked_job = await session.scalar(select(Job).where(Job.id == thread.job_id, Job.owner_id == thread.owner_id))

    # A new chat's placeholder job is not an approved role.
    if linked_job is not None and linked_job.active_version is not None:
        selected_jobs = [linked_job]
    else:
        selected_jobs = list(await session.scalars(
            select(Job).where(Job.owner_id == thread.owner_id, Job.active_version.is_not(None),
                              Job.archived_at.is_(None)).order_by(Job.created_at.desc())
        ))

    activity: list[dict[str, Any]] = []
    for job in selected_jobs:
        version = job.active_version
        cv_count = await session.scalar(select(func.count(Application.id)).where(Application.job_id == job.id))
        stage_counts = dict((stage, count) for stage, count in await session.execute(
            select(Application.stage, func.count(Application.id)).where(Application.job_id == job.id)
            .group_by(Application.stage)
        ))
        posting_rows = list(await session.execute(
            select(JobPosting.platform, JobPosting.status).where(JobPosting.job_id == job.id,
                                                                 JobPosting.version == version)
        ))
        posting_status = next((status for platform, status in posting_rows if platform == "discord"), "not_posted")
        activity.append({
            "job_id": str(job.id), "job_code": job.public_code, "title": job.title,
            "archived": job.archived_at is not None, "version": version, "cv_submissions": cv_count or 0,
            "cv_pipeline": {
                "uploaded": stage_counts.get("uploaded", 0),
                "integrity_check": stage_counts.get("integrity_check", 0),
                "integrity_review": stage_counts.get("integrity_review", 0),
                "parsed": stage_counts.get("parsed", 0),
                "ranking": stage_counts.get("ranking", 0),
                "f1_review": stage_counts.get("f1_review", 0),
                "scoring": stage_counts.get("scoring", 0),
                "scoring_processing": stage_counts.get("scoring_processing", 0),
                "f2_review": stage_counts.get("f2_review", 0),
                "interview_selection": stage_counts.get("interview_selection", 0),
                "rejected": stage_counts.get("rejected", 0),
            },
            "posting_status": posting_status,
            "postings": [{"platform": platform, "status": status} for platform, status in posting_rows],
            "posting_configured": posting_configured,
            "can_post": job.archived_at is None and posting_configured and posting_status in {"not_posted", "failed"},
        })
    return activity
