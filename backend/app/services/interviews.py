"""Interview invitations, short-lived RTC credentials, and ephemeral audio transcription."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from livekit import api as livekit_api
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Application, Candidate, InterviewSession, Job
from app.db.models import GoogleCalendarConnection
from app.services import google_calendar_mcp

logger = logging.getLogger("app.interviews")


def new_invitation(scheduled_at: datetime, ttl_hours: int) -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(40)
    expiry = max(datetime.now(timezone.utc), scheduled_at.astimezone(timezone.utc)) + timedelta(hours=ttl_hours)
    return token, hashlib.sha256(token.encode()).hexdigest(), expiry


def invitation_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _calendar_event_id(result: dict) -> str | None:
    """Extract the event identifier from Google's MCP tool result shapes."""
    if result.get("id"):
        return str(result["id"])
    for key in ("event", "data", "result"):
        nested = result.get(key)
        if isinstance(nested, dict):
            found = _calendar_event_id(nested)
            if found:
                return found
    return None


async def deliver_calendar_invitation(session: AsyncSession, settings: Settings, record: InterviewSession,
                                      candidate: Candidate, job: Job, token: str,
                                      replacement: bool = False,
                                      previous_candidate_email: str | None = None) -> str:
    """Send one Calendar attendee invitation when connected, otherwise SMTP fallback."""
    connection = await session.scalar(select(GoogleCalendarConnection).where(
        GoogleCalendarConnection.owner_id == record.owner_id))
    url = f"{settings.web_base_url.rstrip('/')}/interview/{quote(token)}"
    description = (f"HiringCompass private interview for {job.title}.\n\n"
                   f"Candidate: {candidate.full_name}\nJob: {job.title}\n\n"
                   f"Join the interview: {url}\n\n"
                   "The candidate enters a waiting room and must be admitted by the recruiter. "
                   "Both participants acknowledge transcription before joining.")
    if connection is not None:
        try:
            if record.calendar_event_id:
                result = await google_calendar_mcp.update_event(session, record.owner_id, settings,
                    event_id=record.calendar_event_id, start=record.scheduled_at,
                    end=record.scheduled_at + timedelta(minutes=record.duration_minutes),
                    timezone_name=record.timezone_name, description=description,
                    candidate_email=record.candidate_email,
                    previous_candidate_email=previous_candidate_email)
            else:
                result = await google_calendar_mcp.create_event(session, record.owner_id, settings,
                    summary=f"HiringCompass interview · {candidate.full_name} · {job.title}",
                    description=description, candidate_email=record.candidate_email,
                    start=record.scheduled_at,
                    end=record.scheduled_at + timedelta(minutes=record.duration_minutes),
                    timezone_name=record.timezone_name)
            event_id = _calendar_event_id(result)
            if not event_id:
                raise RuntimeError("Google Calendar did not return an event ID.")
            record.calendar_event_id = event_id
            record.calendar_sync_status, record.calendar_sync_error = "synced", None
            delivery = result.get("_delivery_method", "google_calendar")
            record.invitation_delivery = delivery
            record.invitation_sent_at = datetime.now(timezone.utc)
            await session.commit()
            return delivery
        except Exception as exc:
            record.calendar_sync_status = "failed"
            record.calendar_sync_error = str(exc)[:1000]
            await session.commit()
            logger.warning("Google Calendar MCP delivery failed; attempting SMTP fallback",
                           extra={"interview_id": str(record.id), "error": str(exc)[:300]})
    else:
        record.calendar_sync_status = "not_connected"
    await send_invitation(settings, record.candidate_email, candidate.full_name, job.title,
                          record.scheduled_at, record.timezone_name, token, replacement=replacement)
    record.invitation_delivery = "smtp_fallback"
    record.invitation_sent_at = datetime.now(timezone.utc)
    await session.commit()
    return "smtp_fallback"


def rtc_token(settings: Settings, session_id: str, role: str, name: str) -> str:
    identity = f"{role}:{session_id}"
    return (livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(identity).with_name(name)
            .with_grants(livekit_api.VideoGrants(room_join=True, room=f"interview-{session_id}",
                                                 can_publish=True, can_subscribe=True))
            .with_ttl(timedelta(minutes=15)).to_jwt())


async def close_room(settings: Settings, session_id: str) -> None:
    api_url = settings.livekit_url.replace("ws://", "http://", 1).replace("wss://", "https://", 1)
    async with livekit_api.LiveKitAPI(url=api_url,
                                      api_key=settings.livekit_api_key,
                                      api_secret=settings.livekit_api_secret) as client:
        await client.room.delete_room(livekit_api.DeleteRoomRequest(room=f"interview-{session_id}"))


async def send_invitation(settings: Settings, email: str, candidate_name: str, job_title: str,
                          scheduled_at: datetime, timezone_name: str, token: str,
                          replacement: bool = False) -> None:
    if not settings.interview_email or not settings.interview_email_password:
        raise RuntimeError("Set INTERVIEW_EMAIL and INTERVIEW_EMAIL_PASSWORD before sending invitations.")
    url = f"{settings.web_base_url.rstrip('/')}/interview/{quote(token)}"
    message = EmailMessage()
    message["From"] = settings.interview_email
    message["To"] = email
    message["Subject"] = f"{'Updated interview invitation' if replacement else 'Interview invitation'}: {job_title}"
    local_time = scheduled_at.astimezone(ZoneInfo(timezone_name))
    replacement_notice = ("The interview link has changed. Use this newest link; the previous link is no longer valid.\n\n"
                          if replacement else "")
    message.set_content(
        f"Hello {candidate_name},\n\nYour interview for {job_title} is scheduled for "
        f"{local_time.strftime('%Y-%m-%d %H:%M')} ({timezone_name}).\n\n"
        f"{replacement_notice}Join from the same local network using this private link:\n{url}\n\n"
        "You may enter the waiting room early. The recruiter will admit you. "
        "The link expires after the interview window and is replaced if your interview is rescheduled.\n"
    )

    def deliver() -> None:
        with smtplib.SMTP(settings.interview_smtp_host, settings.interview_smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(settings.interview_email, settings.interview_email_password)
            smtp.send_message(message)

    await asyncio.to_thread(deliver)


async def refresh_pending_invitation_links(session_factory: async_sessionmaker[AsyncSession],
                                           settings: Settings) -> int:
    """Email fresh links to sent, pending interviews when the configured public host changed."""
    base_url = settings.web_base_url.rstrip("/")
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        records = list(await session.scalars(select(InterviewSession.id).where(
            InterviewSession.status.in_(["scheduled", "waiting"]),
            InterviewSession.started_at.is_(None), InterviewSession.ended_at.is_(None),
            InterviewSession.invitation_sent_at.is_not(None),
            InterviewSession.invitation_expires_at > now,
            or_(InterviewSession.invitation_base_url.is_(None),
                InterviewSession.invitation_base_url != base_url),
        ).order_by(InterviewSession.scheduled_at)))

    refreshed = 0
    for session_id in records:
        async with session_factory() as session:
            record = await session.scalar(select(InterviewSession).where(
                InterviewSession.id == session_id).with_for_update())
            if (record is None or record.status not in {"scheduled", "waiting"} or
                record.started_at is not None or record.ended_at is not None or
                record.invitation_sent_at is None or record.invitation_expires_at <= now or
                (record.invitation_base_url or "").rstrip("/") == base_url):
                continue
            application = await session.get(Application, record.application_id)
            candidate = await session.get(Candidate, application.candidate_id) if application else None
            job = await session.get(Job, record.job_id)
            if application is None or candidate is None or job is None:
                logger.warning("skipping replacement interview invitation with missing records",
                               extra={"interview_id": str(record.id)})
                continue
            token, digest, expiry = new_invitation(record.scheduled_at, settings.invite_token_ttl_hours)
            try:
                await send_invitation(settings, record.candidate_email, candidate.full_name, job.title,
                                      record.scheduled_at, record.timezone_name, token, replacement=True)
            except Exception:
                logger.exception("could not send replacement interview invitation",
                                 extra={"interview_id": str(record.id)})
                continue
            record.invitation_hash, record.invitation_expires_at = digest, expiry
            record.invitation_base_url = base_url
            record.invitation_sent_at = datetime.now(timezone.utc)
            await session.commit()
            refreshed += 1
    if refreshed:
        logger.info("replacement interview invitations sent", extra={"count": refreshed})
    return refreshed


async def transcribe_segment(settings: Settings, audio: bytes, content_type: str) -> str:
    if not settings.groq_api_key:
        raise RuntimeError("Set GROQ_API_KEY before starting live transcription.")
    if len(audio) > 5 * 1024 * 1024:
        raise ValueError("The audio segment is too large.")
    extension = "webm" if "webm" in content_type else "ogg" if "ogg" in content_type else "wav"
    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            data={"model": settings.groq_whisper_model, "language": "en", "response_format": "verbose_json",
                  "temperature": "0"},
            files={"file": (f"segment.{extension}", audio, content_type)},
        )
        response.raise_for_status()
        return usable_transcript(response.json())


def usable_transcript(payload: dict) -> str:
    """Discard Whisper segments whose metadata indicates likely silence or unreliable text."""
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return str(payload.get("text") or "").strip()
    accepted: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        no_speech = segment.get("no_speech_prob")
        logprob = segment.get("avg_logprob")
        compression = segment.get("compression_ratio")
        if isinstance(no_speech, (int, float)) and no_speech >= 0.6:
            continue
        if isinstance(logprob, (int, float)) and logprob < -1.0:
            continue
        if isinstance(compression, (int, float)) and compression > 2.8:
            continue
        text = str(segment.get("text") or "").strip()
        if text:
            accepted.append(text)
    return " ".join(accepted)
