"""Single-candidate interview scheduling through the recruiter chat.

Calendar checks and confirmation remain here; chat.py only dispatches the request.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, log_exception
from app.db.models import Application, Candidate, ChatMessage, ChatThread, GoogleCalendarConnection, InterviewSession, Job
from app.services import google_calendar_mcp, jobs
from app.services.interview_chat import parse_interview_time
from app.services.interviews import deliver_calendar_invitation, new_invitation

logger = get_logger("services.interview_scheduling")


def chat_interview_time(text: str, zone_name: str, now: datetime) -> datetime | None:
    try:
        return parse_interview_time(text, zone_name, now)
    except ZoneInfoNotFoundError:
        return None


def _slot_iso(slot: dict[str, Any], edge: str) -> str | None:
    value = slot.get(edge)
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date_time")
    if not value:
        value = slot.get(f"{edge}Time") or slot.get(f"{edge}_time")
    return str(value) if value else None


async def handle_interview_invitation(session: AsyncSession, runtime: Any, thread: ChatThread,
                                       message: str, messages: list[ChatMessage]) -> tuple[str, dict[str, Any]] | None:
    """Chat-mediated single interview scheduling with availability check and explicit final confirmation."""
    lowered = message.strip().lower().rstrip(".!?")
    previous = next((item for item in reversed(messages[:-1]) if item.role == "assistant"), None)
    prior = (previous.payload or {}) if previous else {}
    pending = prior.get("type") == "interview_schedule"
    explicit_candidate = re.search(r"\bcandidate\s*0*(\d{1,6})\b", lowered)
    if pending and explicit_candidate and prior.get("application_id"):
        prior_application = await session.get(Application, uuid.UUID(prior["application_id"]))
        if prior_application is None or prior_application.candidate_number != int(explicit_candidate.group(1)):
            pending, prior = False, {}
    mode = prior.get("mode", "schedule") if pending else (
        "reschedule" if re.search(r"\b(?:reschedule|move)\b", lowered) else "schedule")
    wants_schedule = bool(re.search(r"\b(?:schedule|invite|send|reschedule|move)\b", lowered) and
                          re.search(r"\b(?:interview|candidate|candidates|selected)\b", lowered))
    if not pending and not wants_schedule:
        return None

    if pending and prior.get("awaiting_confirmation") == "calendar_event":
        if lowered not in {"yes", "yes please", "confirm", "confirm interview", "schedule it", "send invitation", "send it", "go ahead"}:
            return "I haven’t scheduled the interview. Reply ‘confirm interview’ to create the Calendar event and email the private link. To change the time, ask me to schedule or reschedule that candidate again with the new time.", prior
        try:
            application_id = uuid.UUID(prior["application_id"])
            job_id = uuid.UUID(prior["job_id"])
            when = datetime.fromisoformat(prior["scheduled_at"])
            duration = int(prior.get("duration_minutes", 30))
        except (KeyError, ValueError, TypeError):
            return "That interview draft expired. Please ask me to schedule the candidate again.", {"type": "text"}
        row = (await session.execute(select(Application, Candidate, Job)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .join(Job, Job.id == Application.job_id)
            .where(Application.id == application_id, Job.owner_id == thread.owner_id,
                   Job.id == job_id, Application.stage == "interview_selection"))).one_or_none()
        if row is None:
            return "That candidate is no longer in Selected for Interview. Please check the Candidates page and try again.", {"type": "text"}
        application, candidate, job = row
        mode = prior.get("mode", "schedule")
        previous_email = None
        if mode == "reschedule":
            try:
                record = await session.scalar(select(InterviewSession).where(
                    InterviewSession.id == uuid.UUID(prior["interview_id"]),
                    InterviewSession.owner_id == thread.owner_id).with_for_update())
            except (KeyError, ValueError, TypeError):
                record = None
            if record is None or record.status in {"live", "ended"}:
                return "That interview can no longer be rescheduled. Open Interviews to check its current status.", {"type": "candidate_work", "route": "/interviews"}
            previous_email = record.candidate_email
            record.scheduled_at = when.astimezone(timezone.utc)
            record.timezone_name = prior["timezone_name"]
            record.candidate_email = prior["candidate_email"]
            record.duration_minutes = duration
            record.status = "scheduled"
            record.candidate_waiting_at = record.candidate_admitted_at = None
            record.candidate_consented_at = record.interviewer_consented_at = None
            record.candidate_joined_at = record.interviewer_joined_at = None
            record.started_at = record.ended_at = None
        else:
            if await session.scalar(select(InterviewSession.id).where(
                InterviewSession.application_id == application.id, InterviewSession.status != "ended")):
                return "This candidate already has an active interview. Say ‘reschedule the interview with this candidate’ to change its time.", {"type": "candidate_work", "route": "/interviews"}
            record = InterviewSession(application_id=application.id, job_id=job.id, owner_id=thread.owner_id,
                candidate_email=prior["candidate_email"], scheduled_at=when.astimezone(timezone.utc),
                timezone_name=prior["timezone_name"], duration_minutes=duration,
                invitation_hash="pending", invitation_expires_at=when.astimezone(timezone.utc),
                invitation_base_url=runtime.deps.settings.web_base_url.rstrip("/"), questions=[])
            session.add(record)
            await session.flush()
        token, digest, expiry = new_invitation(when, runtime.deps.settings.invite_token_ttl_hours)
        record.invitation_hash, record.invitation_expires_at = digest, expiry
        record.invitation_base_url = runtime.deps.settings.web_base_url.rstrip("/")
        record.invitation_sent_at = None
        await session.commit()
        try:
            delivery = await deliver_calendar_invitation(session, runtime.deps.settings, record, candidate, job, token,
                replacement=(mode == "reschedule"), previous_candidate_email=previous_email)
        except Exception as exc:
            await session.rollback()
            log_exception(logger, "chat interview invitation delivery failed", exc,
                          interview_id=str(record.id))
            return "The interview was saved, but neither the Calendar invitation nor SMTP fallback could be delivered. Open Interviews and retry the invitation.", {"type": "candidate_work", "route": "/interviews"}
        delivery_text = ("Google Calendar invitation sent" if delivery in {"google_calendar", "google_calendar_api"}
                         else "Calendar delivery failed, so the SMTP fallback email was sent")
        action_word = "rescheduled" if mode == "reschedule" else "scheduled"
        return (f"Interview {action_word} for {candidate.full_name} ({job.public_code}) at "
                f"{when.strftime('%Y-%m-%d %H:%M %Z')}. {delivery_text}. The invitation contains the private join link.",
                {"type": "candidate_work", "route": "/interviews", "interview_id": str(record.id),
                 "calendar_sync_status": record.calendar_sync_status})

    if pending and prior.get("awaiting_confirmation") == "calendar_time_choice":
        slots = prior.get("available_slots", [])
        choice = re.search(r"\b([1-5])\b", lowered)
        if not choice or int(choice.group(1)) > len(slots):
            return "Choose one of the available times by replying with its number, or provide another date and time.", prior
        selected = slots[int(choice.group(1)) - 1]
        prior = {**prior, "scheduled_at": selected["start"], "awaiting_confirmation": "calendar_event"}
        zone = ZoneInfo(prior["timezone_name"])
        when = datetime.fromisoformat(prior["scheduled_at"]).astimezone(zone)
        return (f"I found this time free: {when.strftime('%A, %B %d at %I:%M %p')} ({prior['timezone_name']}) for "
                f"{prior['candidate_name']}. Reply ‘confirm interview’ to create the event and send the invitation.", prior)

    if pending:
        candidate_id = prior.get("application_id")
        candidate_email = prior.get("candidate_email")
        zone_name = prior.get("timezone_name") or runtime.deps.settings.google_calendar_default_timezone
        duration = int(prior.get("duration_minutes", 30))
        application = await session.get(Application, uuid.UUID(candidate_id)) if candidate_id else None
        candidate = await session.get(Candidate, application.candidate_id) if application else None
        job = await session.get(Job, application.job_id) if application else None
    else:
        candidate_id = None
        candidate_email = None
        zone_name = runtime.deps.settings.google_calendar_default_timezone
        duration = 30
        application = candidate = job = None

    label_match = re.search(r"\bcandidate\s*0*(\d{1,6})\b", lowered)
    if label_match:
        if thread.job_id is None:
            return "Open the approved job’s chat first, or schedule the selected candidate from Interviews.", {"type": "candidate_work", "route": "/interviews"}
        job = await jobs.get_job(session, thread.job_id)
        if job is None or job.active_version is None or job.archived_at is not None:
            return "This chat is not linked to an active approved job. Open its approved job chat and try again.", {"type": "candidate_work", "route": "/interviews"}
        row = (await session.execute(select(Application, Candidate).join(Candidate, Candidate.id == Application.candidate_id)
            .where(Application.job_id == job.id, Application.candidate_number == int(label_match.group(1)),
                   Application.stage == "interview_selection"))).one_or_none()
        if row is None:
            return f"candidate{int(label_match.group(1)):03d} is not currently approved for interview under {job.public_code}.", {"type": "candidate_work", "route": "/candidates?section=interview"}
        application, candidate = row
        if pending and prior.get("application_id") != str(application.id):
            # A newly named candidate starts a fresh request; never carry over the prior candidate's time.
            pending, prior, candidate_email = False, {}, None
            mode, zone_name, duration = ("reschedule" if re.search(r"\breschedule\b", lowered) else "schedule"), runtime.deps.settings.google_calendar_default_timezone, 30
        candidate_id, candidate_email = str(application.id), candidate.email
        if mode == "reschedule":
            active_interview = await session.scalar(select(InterviewSession).where(
                InterviewSession.application_id == application.id,
                InterviewSession.owner_id == thread.owner_id,
                InterviewSession.status.in_(["scheduled", "waiting"]),
                InterviewSession.started_at.is_(None), InterviewSession.ended_at.is_(None)))
            if active_interview is None:
                return f"{candidate.full_name} has no scheduled interview to reschedule.", {"type": "candidate_work", "route": "/interviews"}
            prior = {**prior, "mode": "reschedule", "interview_id": str(active_interview.id)}
        else:
            active_interview = await session.scalar(select(InterviewSession).where(
                InterviewSession.application_id == application.id,
                InterviewSession.owner_id == thread.owner_id)
                .order_by(InterviewSession.created_at.desc()).limit(1))
            if active_interview is not None:
                if active_interview.status == "ended":
                    return (f"{candidate.full_name} has already completed an interview. "
                            "I won't create another invitation from a normal scheduling request. "
                            "Open Interviews to review the completed session.",
                            {"type": "candidate_work", "route": "/interviews"})
                local = active_interview.scheduled_at.astimezone(ZoneInfo(active_interview.timezone_name))
                return (f"{candidate.full_name} already has an interview on {local.strftime('%A, %B %d at %I:%M %p')} "
                        f"({active_interview.timezone_name}). Say ‘reschedule candidate{application.candidate_number:03d}’ "
                        "only if you want to change it.", {"type": "candidate_work", "route": "/interviews"})
    if candidate_email is None:
        email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", message)
        candidate_email = email.group(0).lower() if email else None
    if not candidate_id:
        return "Name one selected candidate, for example ‘schedule an interview with candidate016 tomorrow at 3 pm’.", {"type": "candidate_work", "route": "/candidates?section=interview"}
    if candidate_email is None:
        return (f"I found {candidate.full_name}. What email address should receive the Calendar invitation?",
                {"type": "interview_schedule", "awaiting_details": "candidate_email", "application_id": candidate_id,
                 "candidate_name": candidate.full_name, "job_id": str(job.id), "timezone_name": zone_name,
                 "duration_minutes": duration, "mode": mode, **({"interview_id": prior["interview_id"]} if mode == "reschedule" else {})})
    zone_match = re.search(r"\b(?:timezone|time zone)\s+([A-Za-z_+-]+(?:/[A-Za-z0-9_+.-]+)+)\b", message, re.I)
    if zone_match:
        zone_name = zone_match.group(1)
    parsed_when = chat_interview_time(message, zone_name, datetime.now(timezone.utc))
    if parsed_when is None and pending:
        parsed_when = chat_interview_time(message, zone_name, datetime.now(timezone.utc))
    if parsed_when is None and pending and prior.get("scheduled_at"):
        parsed_when = datetime.fromisoformat(prior["scheduled_at"])
    if parsed_when is None:
        return (f"What date and start time should I use for {candidate.full_name}? Include a timezone if it isn’t {zone_name}; I’ll use a {duration}-minute interview.",
                {"type": "interview_schedule", "awaiting_details": "date_time", "application_id": candidate_id,
                 "candidate_name": candidate.full_name, "candidate_email": candidate_email,
                 "job_id": str(job.id), "timezone_name": zone_name, "duration_minutes": duration,
                 "mode": mode, **({"interview_id": prior["interview_id"]} if mode == "reschedule" else {})})
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        return "I don’t recognize that timezone. Please use an IANA name such as Asia/Dhaka.", {"type": "text"}
    when = parsed_when.astimezone(zone)
    if when.astimezone(timezone.utc) < datetime.now(timezone.utc) + timedelta(minutes=5):
        return "Choose a future time at least five minutes from now.", {"type": "interview_schedule", "awaiting_details": "date_time",
            "application_id": candidate_id, "candidate_name": candidate.full_name, "candidate_email": candidate_email,
            "job_id": str(job.id), "timezone_name": zone_name, "duration_minutes": duration}
    connection = await session.scalar(select(GoogleCalendarConnection).where(
        GoogleCalendarConnection.owner_id == thread.owner_id))
    if connection is None:
        return ("Connect Google Calendar first from the Interviews page, then ask me to schedule this interview again.",
                {"type": "candidate_work", "route": "/interviews", "calendar_connection_required": True})
    try:
        day_start = datetime.combine(when.date(), time(0, 0), tzinfo=zone)
        day_end = day_start + timedelta(days=1)
        exact = await google_calendar_mcp.suggest_time(session, thread.owner_id, runtime.deps.settings,
            connection.account_email or runtime.deps.settings.interview_email, when,
            when + timedelta(minutes=duration), zone_name, duration)
        exact_match = any(_slot_iso(slot, "start") and
            datetime.fromisoformat(_slot_iso(slot, "start")).astimezone(timezone.utc) == when.astimezone(timezone.utc)
            for slot in exact)
        pending_payload = {"type": "interview_schedule", "awaiting_confirmation": "calendar_event",
            "application_id": candidate_id, "candidate_name": candidate.full_name, "candidate_email": candidate_email,
            "job_id": str(job.id), "scheduled_at": when.isoformat(), "timezone_name": zone_name,
            "duration_minutes": duration, "mode": mode,
            **({"interview_id": prior["interview_id"]} if mode == "reschedule" else {})}
        if exact_match:
            return (f"{when.strftime('%A, %B %d at %I:%M %p')} ({zone_name}) is available for {candidate.full_name}. "
                    "Reply ‘confirm interview’ to create the Calendar event and send the private join link.", pending_payload)
        alternatives = await google_calendar_mcp.suggest_time(session, thread.owner_id, runtime.deps.settings,
            connection.account_email or runtime.deps.settings.interview_email, day_start, day_end, zone_name, duration)
        choices: list[dict[str, str]] = []
        for slot in alternatives:
            start_value = _slot_iso(slot, "start")
            end_value = _slot_iso(slot, "end")
            if start_value and end_value:
                choices.append({"start": datetime.fromisoformat(start_value).isoformat(), "end": datetime.fromisoformat(end_value).isoformat()})
            if len(choices) >= 5:
                break
        if not choices:
            return (f"Google Calendar could not confirm that time and returned no alternatives for {when.strftime('%A, %B %d')} . Choose another time and I’ll check again.",
                    {"type": "interview_schedule", "awaiting_details": "date_time", "application_id": candidate_id,
                     "candidate_name": candidate.full_name, "candidate_email": candidate_email, "job_id": str(job.id),
                     "timezone_name": zone_name, "duration_minutes": duration, "mode": mode,
                     **({"interview_id": prior["interview_id"]} if mode == "reschedule" else {})})
        lines = []
        for index, slot in enumerate(choices, 1):
            local = datetime.fromisoformat(slot["start"]).astimezone(zone)
            lines.append(f"{index}. {local.strftime('%A, %B %d at %I:%M %p')}")
        return (f"That time appears busy. Here are available times for {candidate.full_name}:\n" + "\n".join(lines) +
                "\nReply with a number; I’ll ask you to confirm before creating the event.",
                {"type": "interview_schedule", "awaiting_confirmation": "calendar_time_choice",
                 "application_id": candidate_id, "candidate_name": candidate.full_name,
                 "candidate_email": candidate_email, "job_id": str(job.id), "timezone_name": zone_name,
                 "duration_minutes": duration, "mode": mode, "available_slots": choices,
                 **({"interview_id": prior["interview_id"]} if mode == "reschedule" else {})})
    except Exception as exc:
        logger.warning("Google Calendar availability check failed", extra={"error": str(exc)[:300]})
        return ("I couldn’t check Calendar availability. Verify Google Calendar MCP access and try again; no invitation was sent.",
                {"type": "candidate_work", "route": "/interviews", "calendar_error": True})


