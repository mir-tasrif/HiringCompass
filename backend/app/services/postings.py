"""Deliver approved job descriptions to configured hiring platforms."""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, PermanentError, TransientError
from app.db.models import JobPosting
from app.services import jobs


async def post_to_discord(session: AsyncSession, settings: Settings, job_id: uuid.UUID) -> tuple[int, bool]:
    """Post the active approved job version once; return (version, was_already_posted)."""
    if not settings.discord_webhook_url.strip():
        raise PermanentError("Discord posting is not configured. Add DISCORD_WEBHOOK_URL to .env and restart the app.")

    job = await jobs.get_job(session, job_id)
    if job is None or job.active_version is None:
        raise ConflictError("This job has no approved version ready to post.")
    if job.archived_at is not None:
        raise ConflictError("This job is expired and cannot be posted. Restore it from Expired Jobs first.")
    version = await jobs.get_version(session, job_id, job.active_version)
    if version is None or version.status != "active":
        raise ConflictError("This job has no approved version ready to post.")
    content = version.profile.get("posting_text", "").strip()
    if not content:
        raise PermanentError("The approved job description does not contain posting text.")
    body = content.split("\n", 1)[1].strip() if "\n" in content else content
    if len(body) > 4096:
        raise PermanentError("This job description is longer than a single Discord post supports. Shorten it and approve a revised version first.")

    record = await session.scalar(select(JobPosting).where(
        JobPosting.job_id == job_id,
        JobPosting.version == version.version,
        JobPosting.platform == "discord",
    ))
    if record and record.status == "posted":
        return version.version, True
    if record and record.status == "pending":
        raise ConflictError("The Discord post is already being processed. Please check back in a moment.")

    if record is None:
        record = JobPosting(job_id=job_id, version=version.version, platform="discord", status="pending")
        session.add(record)
    else:
        record.status, record.error = "pending", None
    await session.commit()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                settings.discord_webhook_url,
                json={"embeds": [{"title": job.title, "description": body, "color": 0x2563EB}],
                      "allowed_mentions": {"parse": []}},
            )
            response.raise_for_status()
            try:
                record.external_id = response.json().get("id")
            except (ValueError, AttributeError):
                record.external_id = None
    except httpx.TimeoutException as exc:
        record.status, record.error = "failed", "Discord webhook timed out."
        await session.commit()
        raise TransientError("Discord did not respond in time. You can ask me to retry.") from exc
    except httpx.HTTPStatusError as exc:
        record.status, record.error = "failed", f"Discord returned HTTP {exc.response.status_code}."
        await session.commit()
        if exc.response.status_code >= 500 or exc.response.status_code == 429:
            raise TransientError("Discord is temporarily unavailable. You can ask me to retry.") from exc
        raise PermanentError("Discord rejected the webhook request. Check that DISCORD_WEBHOOK_URL is valid.") from exc
    except httpx.HTTPError as exc:
        record.status, record.error = "failed", "Could not connect to Discord."
        await session.commit()
        raise TransientError("I couldn't connect to Discord. You can ask me to retry.") from exc

    record.status, record.error = "posted", None
    await session.commit()
    return version.version, False
