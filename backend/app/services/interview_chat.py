"""Database-backed candidate roster and recruiter-confirmed interview batch planning."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Application, Candidate, GoogleCalendarConnection, InterviewSession, Job
from app.services import google_calendar_mcp
from app.services.cv_pipeline import extract_candidate_email
from app.services.interviews import deliver_calendar_invitation, new_invitation

ZONE = "Asia/Dhaka"
INTERVIEW_MINUTES = 30
GAP_MINUTES = 10
OFFICE_OPEN = time(9, 0)
OFFICE_CLOSE = time(17, 0)
ACTIVE_STATUSES = ("scheduled", "waiting", "live")
CONFIRM = {"confirm schedule", "confirm interviews", "confirm interview", "confirm and send invitations",
           "yes", "yes please", "confirm", "go ahead", "yes, schedule them", "schedule them"}


def parse_interview_time(text: str, zone_name: str, now: datetime) -> datetime | None:
    """Accept an explicit date or today/tomorrow with an ordinary clock time."""
    zone = ZoneInfo(zone_name)
    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})[ T]+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.I)
    day_match = re.search(r"\b(today|tomorrow)\b", text, re.I)
    clock = date_match or (re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.I) if day_match else None)
    if not clock:
        # A 24-hour time such as "today 10:00" is also unambiguous.
        clock = re.search(r"\b(?:at\s+)?(\d{1,2}):(\d{2})\b", text, re.I) if day_match else None
    if not clock:
        return None
    try:
        if date_match:
            date = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
            hour, minute, meridiem = int(date_match.group(2)), int(date_match.group(3) or 0), date_match.group(4)
        else:
            date = now.astimezone(zone).date() + timedelta(days=1 if day_match.group(1).lower() == "tomorrow" else 0)
            hour, minute, meridiem = int(clock.group(1)), int(clock.group(2) or 0), clock.group(3) if clock.lastindex and clock.lastindex >= 3 else None
        if meridiem:
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
        if hour > 23 or minute > 59:
            return None
        return datetime.combine(date, time(hour, minute), tzinfo=zone)
    except ValueError:
        return None


def office_cursor(value: datetime) -> datetime:
    """Move a proposed start into Mon–Fri 09:00–17:00, leaving valid minutes intact."""
    value = value.astimezone(ZoneInfo(ZONE))
    while True:
        if value.weekday() >= 5:
            value = datetime.combine(value.date() + timedelta(days=1), OFFICE_OPEN, tzinfo=value.tzinfo)
            continue
        opening = datetime.combine(value.date(), OFFICE_OPEN, tzinfo=value.tzinfo)
        closing = datetime.combine(value.date(), OFFICE_CLOSE, tzinfo=value.tzinfo)
        value = max(value, opening)
        if value + timedelta(minutes=INTERVIEW_MINUTES) <= closing:
            return value
        value = datetime.combine(value.date() + timedelta(days=1), OFFICE_OPEN, tzinfo=value.tzinfo)


def slot_start(slot: dict[str, Any]) -> datetime | None:
    value = slot.get("start") or slot.get("startTime") or slot.get("start_time")
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date_time")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


async def selected_roster(session: AsyncSession, owner_id: uuid.UUID, job_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.execute(select(Application, Candidate).join(Candidate, Candidate.id == Application.candidate_id)
        .join(Job, Job.id == Application.job_id).where(Job.id == job_id, Job.owner_id == owner_id,
            Application.stage == "interview_selection").order_by(Application.candidate_number))
    roster = []
    for application, candidate in rows:
        existing = await session.scalar(select(InterviewSession).where(
            InterviewSession.application_id == application.id, InterviewSession.owner_id == owner_id)
            .order_by(InterviewSession.created_at.desc()).limit(1))
        roster.append({"application_id": str(application.id), "number": application.candidate_number,
                       "label": f"candidate{application.candidate_number:03d}",
                       "name": candidate.full_name or f"candidate{application.candidate_number:03d}",
                       "email": candidate.email, "interview_id": str(existing.id) if existing else None,
                       "interview_status": existing.status if existing else None,
                       "scheduled_at": existing.scheduled_at.isoformat() if existing else None,
                       "invitation_sent": bool(existing.invitation_sent_at) if existing else False})
    return roster


def roster_reply(job: Job, roster: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    if not roster:
        return (f"{job.public_code} currently has no candidates recorded as Selected for Interview. "
                "If you approved them in Review, check that this is the same job and refresh its candidate stage.",
                {"type": "interview_roster", "job_id": str(job.id), "candidates": [],
                 "route": f"/candidates?jobId={job.id}&section=interview"})
    lines = [f"Selected for Interview under {job.public_code} ({len(roster)}):"]
    for entry in roster:
        if entry["interview_id"]:
            when = datetime.fromisoformat(entry["scheduled_at"]).astimezone(ZoneInfo(ZONE)) if entry["scheduled_at"] else None
            if entry["interview_status"] == "ended":
                status = "interview completed"
            elif entry["invitation_sent"]:
                status = "scheduled " + when.strftime("%a %d %b, %I:%M %p") if when else "scheduled"
            else:
                status = "invitation needs retry"
        else:
            status = "ready to schedule" if entry["email"] else "email needed"
        name = entry["name"] if entry["name"].casefold() != entry["label"].casefold() else ""
        address = f" · {entry['email']}" if entry["email"] else ""
        lines.append(f"- {entry['label']}{' — ' + name if name else ''}{address}: {status}")
    return "\n".join(lines), {"type": "interview_roster", "job_id": str(job.id), "candidates": roster,
                              "route": f"/candidates?jobId={job.id}&section=interview"}


async def _free_slot(session: AsyncSession, settings: Settings, owner_id: uuid.UUID,
                     email: str, cursor: datetime) -> datetime:
    connection = await session.scalar(select(GoogleCalendarConnection).where(GoogleCalendarConnection.owner_id == owner_id))
    if connection is None:
        raise ValueError("Connect Google Calendar from Interviews before scheduling a batch.")
    for _ in range(45):
        cursor = office_cursor(cursor)
        close = datetime.combine(cursor.date(), OFFICE_CLOSE, tzinfo=cursor.tzinfo)
        choices = await google_calendar_mcp.suggest_time(session, owner_id, settings,
            connection.account_email or settings.interview_email, cursor, close, ZONE, INTERVIEW_MINUTES)
        for choice in choices:
            start = slot_start(choice)
            if not start:
                continue
            start = start.astimezone(cursor.tzinfo)
            end = start + timedelta(minutes=INTERVIEW_MINUTES)
            if start < cursor or start.weekday() >= 5 or end > close:
                continue
            conflicts = await session.scalars(select(InterviewSession).where(
                InterviewSession.owner_id == owner_id, InterviewSession.status.in_(ACTIVE_STATUSES),
                InterviewSession.scheduled_at < end.astimezone(timezone.utc)))
            if any(start < existing.scheduled_at.astimezone(cursor.tzinfo) + timedelta(minutes=existing.duration_minutes)
                   for existing in conflicts):
                continue
            return start
        cursor = datetime.combine(cursor.date() + timedelta(days=1), OFFICE_OPEN, tzinfo=cursor.tzinfo)
    raise ValueError("I couldn't find enough free office-hour slots in the next 45 days.")


async def _plan(session: AsyncSession, settings: Settings, owner_id: uuid.UUID, job: Job,
                requested: datetime) -> tuple[str, dict[str, Any]]:
    roster = await selected_roster(session, owner_id, job.id)
    remaining = [entry for entry in roster if not entry["interview_id"]]
    if not remaining:
        return (f"No unscheduled selected candidates remain for {job.public_code}. Ask to reschedule a named candidate if a time must change.",
                {"type": "interview_roster", "job_id": str(job.id), "candidates": roster, "route": "/interviews"})
    missing = [entry["label"] for entry in remaining if not entry["email"] or
               extract_candidate_email(entry["email"]) != entry["email"].strip().lower()]
    if missing:
        return ("Add or correct an email for " + ", ".join(missing) + " before I send a group invitation.",
                {"type": "interview_roster", "job_id": str(job.id), "candidates": roster,
                 "route": f"/candidates?jobId={job.id}&section=interview"})
    now = datetime.now(timezone.utc).astimezone(ZoneInfo(ZONE))
    adjusted = requested.astimezone(ZoneInfo(ZONE)) < now + timedelta(minutes=5)
    cursor = office_cursor(max(requested.astimezone(ZoneInfo(ZONE)), now + timedelta(minutes=5)))
    entries: list[dict[str, str]] = []
    for candidate in remaining:
        start = await _free_slot(session, settings, owner_id, candidate["email"], cursor)
        entries.append({"application_id": candidate["application_id"], "label": candidate["label"],
                        "name": candidate["name"], "email": candidate["email"], "start": start.isoformat()})
        cursor = start + timedelta(minutes=INTERVIEW_MINUTES + GAP_MINUTES)
    lines = [f"Proposed interviews for {job.public_code} (Asia/Dhaka; Monday–Friday, 9 AM–5 PM):"]
    if adjusted:
        lines.append("Your requested start time has passed; the first available future office-hour slot is shown below.")
    for entry in entries:
        start = datetime.fromisoformat(entry["start"])
        lines.append(f"- {entry['label']} — {start.strftime('%a %d %b, %I:%M %p')}–"
                     f"{(start + timedelta(minutes=INTERVIEW_MINUTES)).strftime('%I:%M %p')}")
    lines.append("Each interview is 30 minutes with a 10-minute gap. Reply ‘confirm schedule’ to send all invitations.")
    return "\n".join(lines), {"type": "interview_batch", "state": "preview", "job_id": str(job.id),
                               "timezone_name": ZONE, "duration_minutes": INTERVIEW_MINUTES,
                               "gap_minutes": GAP_MINUTES, "requested_start": requested.isoformat(), "entries": entries}


async def _confirm(session: AsyncSession, settings: Settings, owner_id: uuid.UUID, job: Job,
                   prior: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    entries = prior.get("entries") or []
    roster = await selected_roster(session, owner_id, job.id)
    remaining = [entry for entry in roster if not entry["interview_id"]]
    if {entry["application_id"] for entry in remaining} != {entry["application_id"] for entry in entries}:
        return await _plan(session, settings, owner_id, job, datetime.fromisoformat(prior["requested_start"]))
    for entry in entries:
        current = next((candidate for candidate in remaining if candidate["application_id"] == entry["application_id"]), None)
        if current is None or current["email"] != entry["email"]:
            return await _plan(session, settings, owner_id, job, datetime.fromisoformat(prior["requested_start"]))
        start = datetime.fromisoformat(entry["start"])
        if start.astimezone(timezone.utc) < datetime.now(timezone.utc) + timedelta(minutes=5):
            updated, payload = await _plan(session, settings, owner_id, job, datetime.now(ZoneInfo(ZONE)))
            return "The proposed time has passed. Please review the new proposal.\n" + updated, payload
        try:
            available = await _free_slot(session, settings, owner_id, current["email"], start)
        except Exception:
            return ("I couldn't recheck Google Calendar availability. No invitations were sent. "
                    "Check the Calendar connection and confirm again.", prior)
        if available != start:
            updated, payload = await _plan(session, settings, owner_id, job, datetime.fromisoformat(prior["requested_start"]))
            return "Calendar availability changed. Please review the new proposal.\n" + updated, payload
    outcomes = []
    failed_application_ids = []
    for entry in entries:
        application = await session.scalar(select(Application).where(
            Application.id == uuid.UUID(entry["application_id"])).with_for_update())
        candidate = await session.get(Candidate, application.candidate_id) if application else None
        if not application or application.stage != "interview_selection" or not candidate:
            outcomes.append(f"- {entry['label']}: skipped; candidate is no longer selected")
            continue
        existing = await session.scalar(select(InterviewSession.id).where(
            InterviewSession.application_id == application.id))
        if existing:
            outcomes.append(f"- {entry['label']}: skipped; already scheduled")
            continue
        start = datetime.fromisoformat(entry["start"])
        token, digest, expiry = new_invitation(start, settings.invite_token_ttl_hours)
        record = InterviewSession(application_id=application.id, job_id=job.id, owner_id=owner_id,
            candidate_email=entry["email"], scheduled_at=start.astimezone(timezone.utc), timezone_name=ZONE,
            duration_minutes=INTERVIEW_MINUTES, invitation_hash=digest, invitation_expires_at=expiry,
            invitation_base_url=settings.web_base_url.rstrip("/"), questions=[])
        session.add(record)
        await session.commit()
        try:
            await deliver_calendar_invitation(session, settings, record, candidate, job, token)
            outcomes.append(f"- {entry['label']}: invitation sent for {start.strftime('%a %d %b, %I:%M %p')}")
        except Exception:
            await session.rollback()
            outcomes.append(f"- {entry['label']}: interview saved, invitation failed; retry from Interviews")
            failed_application_ids.append(entry["application_id"])
    return (f"Scheduling result for {job.public_code}:\n" + "\n".join(outcomes),
            {"type": "interview_batch", "state": "result", "job_id": str(job.id), "entries": entries,
             "failed_application_ids": failed_application_ids, "route": "/interviews"})


async def _retry_failed(session: AsyncSession, settings: Settings, owner_id: uuid.UUID,
                        job: Job) -> tuple[str, dict[str, Any]]:
    rows = await session.execute(select(InterviewSession, Application, Candidate)
        .join(Application, Application.id == InterviewSession.application_id)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .where(InterviewSession.job_id == job.id, InterviewSession.owner_id == owner_id,
               InterviewSession.status.in_(("scheduled", "waiting")),
               InterviewSession.invitation_sent_at.is_(None)))
    pending = list(rows)
    if not pending:
        return "There are no failed interview invitations to retry for this job.", {"type": "interview_batch", "state": "result", "job_id": str(job.id), "route": "/interviews"}
    outcomes, failed = [], []
    for record, application, candidate in pending:
        label = f"candidate{application.candidate_number:03d}"
        if application.stage != "interview_selection":
            outcomes.append(f"- {label}: skipped; no longer selected for interview")
            continue
        token, digest, expiry = new_invitation(record.scheduled_at, settings.invite_token_ttl_hours)
        record.invitation_hash, record.invitation_expires_at = digest, expiry
        await session.commit()
        try:
            await deliver_calendar_invitation(session, settings, record, candidate, job, token, replacement=True)
            outcomes.append(f"- {label}: invitation sent")
        except Exception:
            await session.rollback()
            failed.append(str(application.id))
            outcomes.append(f"- {label}: still failed; check the Calendar/SMTP connection in Interviews")
    return ("Invitation retry results:\n" + "\n".join(outcomes),
            {"type": "interview_batch", "state": "result", "job_id": str(job.id),
             "failed_application_ids": failed, "route": "/interviews"})


async def handle_interview_chat(session: AsyncSession, settings: Settings, owner_id: uuid.UUID,
                                job_id: uuid.UUID | None, message: str,
                                previous: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Handle roster and group requests before the single-candidate chat flow."""
    lowered = message.strip().lower().rstrip(".!?")
    batch_request = bool(re.search(r"\b(schedule|invite|send)\b", lowered) and
        re.search(r"\b(all|every|remaining|rest|other|them|those)\b", lowered) and
        re.search(r"\b(candidate|candidates|interview|invitation|them|those)\b", lowered))
    list_request = bool(re.search(r"\b(list|show|give|who|which|available)\b", lowered) and
        re.search(r"\b(candidate|candidates|selected)\b", lowered) and not batch_request)
    retry_request = bool(re.search(r"\bretry\b.*\b(?:failed\s+)?(?:interview\s+)?invitations?\b", lowered))
    pending = previous.get("type") == "interview_batch"
    if not (batch_request or list_request or retry_request or pending):
        return None
    if pending and not batch_request and re.search(r"\bcandidate\s*0*\d{1,6}\b", lowered):
        return None  # An explicitly named candidate starts the single-interview flow.
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id,
        Job.active_version.is_not(None), Job.archived_at.is_(None))) if job_id else None
    if job is None:
        return "Open the approved job's chat to list or schedule its selected candidates.", {"type": "text"}
    if list_request:
        return roster_reply(job, await selected_roster(session, owner_id, job.id))
    if retry_request:
        return await _retry_failed(session, settings, owner_id, job)
    if pending and lowered in {"cancel", "never mind", "stop scheduling"}:
        return "Okay. No group invitations were sent.", {"type": "text"}
    if pending and previous.get("state") == "preview" and lowered in CONFIRM:
        if previous.get("job_id") != str(job.id):
            return "The job changed. Ask me to prepare a new schedule.", {"type": "text"}
        return await _confirm(session, settings, owner_id, job, previous)
    if pending and previous.get("state") == "preview":
        revised = parse_interview_time(message, ZONE, datetime.now(timezone.utc))
        if revised is not None:
            try:
                return await _plan(session, settings, owner_id, job, revised)
            except Exception:
                return "I couldn't check Calendar availability for that change. The previous proposal has not been sent.", previous
    if not batch_request and not (pending and previous.get("state") == "need_time"):
        return None
    parsed = parse_interview_time(message, ZONE, datetime.now(timezone.utc))
    if parsed is None and batch_request:
        return (f"What date and start time should I use for the remaining selected candidates under {job.public_code}? "
                "I’ll use Asia/Dhaka and propose weekday office-hour slots.",
                {"type": "interview_batch", "state": "need_time", "job_id": str(job.id)})
    if parsed is None:
        return "Please give a start such as ‘today at 10 AM’ or ‘2026-10-15 10:00’ (Asia/Dhaka).", previous
    try:
        return await _plan(session, settings, owner_id, job, parsed)
    except Exception:
        return ("I couldn't verify enough free times on Google Calendar. No invitations were sent. "
                "Check the Calendar connection and try again.", {"type": "interview_batch", "state": "need_time", "job_id": str(job.id)})
