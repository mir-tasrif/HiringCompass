"""Interview invitations, short-lived RTC credentials, and ephemeral audio transcription."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from livekit import api as livekit_api

from app.core.config import Settings


def new_invitation(scheduled_at: datetime, ttl_hours: int) -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(40)
    expiry = max(datetime.now(timezone.utc), scheduled_at.astimezone(timezone.utc)) + timedelta(hours=ttl_hours)
    return token, hashlib.sha256(token.encode()).hexdigest(), expiry


def invitation_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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
                          scheduled_at: datetime, timezone_name: str, token: str) -> None:
    if not settings.interview_email or not settings.interview_email_password:
        raise RuntimeError("Set INTERVIEW_EMAIL and INTERVIEW_EMAIL_PASSWORD before sending invitations.")
    url = f"{settings.web_base_url.rstrip('/')}/interview/{quote(token)}"
    message = EmailMessage()
    message["From"] = settings.interview_email
    message["To"] = email
    message["Subject"] = f"Interview invitation: {job_title}"
    local_time = scheduled_at.astimezone(ZoneInfo(timezone_name))
    message.set_content(
        f"Hello {candidate_name},\n\nYour interview for {job_title} is scheduled for "
        f"{local_time.strftime('%Y-%m-%d %H:%M')} ({timezone_name}).\n\n"
        f"Join from the same local network using this private link:\n{url}\n\n"
        "You may enter the waiting room early. The recruiter will admit you. "
        "The link expires after the interview window and is replaced if your interview is rescheduled.\n"
    )

    def deliver() -> None:
        with smtplib.SMTP(settings.interview_smtp_host, settings.interview_smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(settings.interview_email, settings.interview_email_password)
            smtp.send_message(message)

    await asyncio.to_thread(deliver)


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
