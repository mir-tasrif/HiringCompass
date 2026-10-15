"""Recruiter's Google Calendar connection endpoints, including the OAuth callback."""

from __future__ import annotations

import html
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_interviewer
from app.api.schemas.interviews import CalendarConnectOut
from app.core.config import get_settings
from app.db.models import GoogleCalendarConnection, User
from app.db.session import get_session
from app.services import google_calendar_mcp

router = APIRouter(prefix="/interviews", tags=["interviews"])
settings = get_settings()
logger = logging.getLogger("app.api.calendar_connection")


@router.get("/google-calendar/status", response_model=CalendarConnectOut)
async def google_calendar_status(user: User = Depends(get_current_interviewer),
                                 session: AsyncSession = Depends(get_session)) -> CalendarConnectOut:
    connection = await session.scalar(select(GoogleCalendarConnection).where(
        GoogleCalendarConnection.owner_id == user.id))
    return CalendarConnectOut(connected=connection is not None,
                              account_email=connection.account_email if connection else None)


@router.post("/google-calendar/connect", response_model=CalendarConnectOut)
async def google_calendar_connect(user: User = Depends(get_current_interviewer)) -> CalendarConnectOut:
    try:
        url = google_calendar_mcp.authorization_url(str(user.id), settings)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return CalendarConnectOut(connected=False, authorization_url=url)


@router.delete("/google-calendar/connect")
async def google_calendar_disconnect(user: User = Depends(get_current_interviewer),
                                     session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    connection = await session.scalar(select(GoogleCalendarConnection).where(
        GoogleCalendarConnection.owner_id == user.id))
    if connection is not None:
        await session.delete(connection)
        await session.commit()
    return {"disconnected": True}


@router.get("/google-calendar/callback", response_class=HTMLResponse)
async def google_calendar_callback(code: str | None = None, state: str | None = None,
                                   error: str | None = None,
                                   session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    if error or not code or not state:
        raise HTTPException(400, "Google Calendar authorization was not completed.")
    try:
        await google_calendar_mcp.complete_oauth(session, code, state, settings)
    except Exception as exc:
        logger.warning("Google Calendar OAuth callback failed", extra={"error": str(exc)[:300]})
        raise HTTPException(400, "Google Calendar connection failed. Check the OAuth callback URL, consent, and Calendar MCP API setup, then try again.") from exc
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>Calendar connected</title></head>
<body style='font:16px Arial,sans-serif;max-width:620px;margin:10vh auto;padding:24px;color:#18324b'>
<h2>Google Calendar connected</h2><p>You can close this tab and return to HiringCompass.</p>
<p><a href='{html.escape(settings.google_calendar_return_url, quote=True)}'>Return to HiringCompass</a></p></body></html>""")
