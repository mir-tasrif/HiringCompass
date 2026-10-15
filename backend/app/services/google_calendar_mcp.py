"""Calendar MCP tool calls with direct Google API fallback when preview access is denied."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.google_calendar_auth import (authorization_url, complete_oauth, decrypt_token,
                                               make_oauth_state, verify_oauth_state, _access_token)

CALENDAR_API_URL = "https://www.googleapis.com/calendar/v3"


def _preview_access_denied(exc: Exception) -> bool:
    """Google's MCP tools are currently limited to preview-enrolled projects."""
    message = str(exc).lower()
    return "developer preview program" in message and "enroll" in message


def _decode_mcp_response(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    body = response.text
    if "text/event-stream" in content_type:
        data = [line[6:].strip() for line in body.splitlines() if line.startswith("data:")]
        body = data[-1] if data else "{}"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError("Google Calendar MCP returned an unreadable response.") from None
    if parsed.get("error"):
        message = parsed["error"].get("message", "Google Calendar MCP request failed.")
        raise RuntimeError(str(message))
    result = parsed.get("result") or {}
    if result.get("isError"):
        text = " ".join(str(part.get("text", "")) for part in result.get("content", []) if isinstance(part, dict))
        raise RuntimeError(text or "Google Calendar MCP tool failed.")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                value = json.loads(item.get("text", ""))
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
    return result


async def call_tool(session: AsyncSession, owner_id: Any, settings: Settings,
                    tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token, _ = await _access_token(session, owner_id, settings)
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if settings.google_calendar_project_id:
        headers["x-goog-user-project"] = settings.google_calendar_project_id
    payload = {"jsonrpc": "2.0", "id": secrets.randbelow(2**31), "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}
    async with httpx.AsyncClient(timeout=35) as http:
        response = await http.post(settings.google_calendar_mcp_url, headers=headers, json=payload)
        response.raise_for_status()
        return _decode_mcp_response(response)


async def _calendar_api_request(session: AsyncSession, owner_id: Any, settings: Settings,
                                method: str, path: str, *, params: dict[str, Any] | None = None,
                                body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Use the public Calendar API only when Google denies Developer Preview MCP access."""
    access_token, _ = await _access_token(session, owner_id, settings)
    async with httpx.AsyncClient(timeout=35) as http:
        response = await http.request(method, f"{CALENDAR_API_URL}{path}",
            headers={"Authorization": f"Bearer {access_token}"}, params=params, json=body)
    if not response.is_success:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        raise RuntimeError(f"Google Calendar API failed (HTTP {response.status_code}): {detail or response.reason_phrase}")
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("Google Calendar API returned an invalid response.")
    return result


def _api_event_time(value: dict[str, Any], zone: ZoneInfo) -> datetime | None:
    timestamp = value.get("dateTime")
    if timestamp:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    date = value.get("date")
    return datetime.fromisoformat(str(date)).replace(tzinfo=zone) if date else None


async def _suggest_time_api(session: AsyncSession, owner_id: Any, settings: Settings,
                            start: datetime, end: datetime, timezone_name: str,
                            duration_minutes: int) -> list[dict[str, Any]]:
    """Find openings on the recruiter's primary calendar with the existing events scope."""
    zone = ZoneInfo(timezone_name)
    busy: list[tuple[datetime, datetime]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                                  "timeZone": timezone_name, "singleEvents": "true", "maxResults": 250}
        if page_token:
            params["pageToken"] = page_token
        result = await _calendar_api_request(session, owner_id, settings, "GET",
            "/calendars/primary/events", params=params)
        for event in result.get("items", []):
            if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                continue
            event_start = _api_event_time(event.get("start") or {}, zone)
            event_end = _api_event_time(event.get("end") or {}, zone)
            if event_start and event_end:
                busy.append((event_start, event_end))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    length = timedelta(minutes=duration_minutes)
    cursor = start
    if end - start >= timedelta(hours=12):
        cursor = max(start, start.astimezone(zone).replace(hour=9, minute=0, second=0, microsecond=0))
    slots: list[dict[str, Any]] = []
    while cursor + length <= end and len(slots) < 5:
        slot_end = cursor + length
        if not any(cursor < occupied_end and slot_end > occupied_start for occupied_start, occupied_end in busy):
            slots.append({"start": {"dateTime": cursor.isoformat()},
                          "end": {"dateTime": slot_end.isoformat()}})
        cursor += timedelta(minutes=30)
    return slots


async def suggest_time(session: AsyncSession, owner_id: Any, settings: Settings,
                       email: str, start: datetime, end: datetime, timezone_name: str,
                       duration_minutes: int = 30) -> list[dict[str, Any]]:
    try:
        result = await call_tool(session, owner_id, settings, "suggest_time", {
            "attendeeEmails": [email], "startTime": start.isoformat(), "endTime": end.isoformat(),
            "timeZone": timezone_name, "durationMinutes": duration_minutes,
            "preferences": {"excludeWeekends": False, "pageSize": 5}})
    except Exception as exc:
        if not _preview_access_denied(exc):
            raise
        return await _suggest_time_api(session, owner_id, settings, start, end,
                                       timezone_name, duration_minutes)
    slots = result.get("timeSlots") or result.get("time_slots") or []
    return [item for item in slots if isinstance(item, dict)]


async def create_event(session: AsyncSession, owner_id: Any, settings: Settings, *,
                       summary: str, description: str, candidate_email: str,
                       start: datetime, end: datetime, timezone_name: str) -> dict[str, Any]:
    try:
        return await call_tool(session, owner_id, settings, "create_event", {
            "summary": summary, "description": description, "startTime": start.isoformat(),
            "endTime": end.isoformat(), "timeZone": timezone_name,
            "attendees": [{"email": candidate_email}],
            "notificationLevel": "ALL", "visibility": "private"})
    except Exception as exc:
        if not _preview_access_denied(exc):
            raise
        result = await _calendar_api_request(session, owner_id, settings, "POST",
            "/calendars/primary/events", params={"sendUpdates": "all"}, body={
                "summary": summary, "description": description,
                "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
                "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
                "attendees": [{"email": candidate_email}], "visibility": "private"})
        result["_delivery_method"] = "google_calendar_api"
        return result


async def update_event(session: AsyncSession, owner_id: Any, settings: Settings, *,
                       event_id: str, start: datetime, end: datetime, timezone_name: str,
                       description: str, candidate_email: str,
                       previous_candidate_email: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"eventId": event_id, "startTime": start.isoformat(),
        "endTime": end.isoformat(), "timeZone": timezone_name,
        "notificationLevel": "ALL", "description": description}
    if previous_candidate_email and previous_candidate_email != candidate_email:
        arguments["addedAttendees"] = [{"email": candidate_email}]
        arguments["removedAttendeeEmails"] = [previous_candidate_email]
    try:
        return await call_tool(session, owner_id, settings, "update_event", arguments)
    except Exception as exc:
        if not _preview_access_denied(exc):
            raise
        body: dict[str, Any] = {"description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone_name}}
        if previous_candidate_email and previous_candidate_email != candidate_email:
            body["attendees"] = [{"email": candidate_email}]
        result = await _calendar_api_request(session, owner_id, settings, "PATCH",
            f"/calendars/primary/events/{event_id}", params={"sendUpdates": "all"}, body=body)
        result["_delivery_method"] = "google_calendar_api"
        return result
