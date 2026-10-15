"""Unit coverage for the Google Calendar MCP adapter and OAuth state protection."""

import asyncio
import json
import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.config import Settings
from app.services import google_calendar_mcp
from app.services.chat import _chat_interview_time


def test_calendar_connection_routes_keep_their_public_paths():
    """OAuth redirect and settings controls must survive router reorganization."""
    from app.main import app

    routes = {(route.path, method) for route in app.routes
              for method in getattr(route, "methods", set())}
    assert ("/interviews/google-calendar/status", "GET") in routes
    assert ("/interviews/google-calendar/connect", "POST") in routes
    assert ("/interviews/google-calendar/connect", "DELETE") in routes
    assert ("/interviews/google-calendar/callback", "GET") in routes


def _settings() -> Settings:
    return Settings(_env_file=None, jwt_secret="test-only-secret-that-is-long-enough")


def test_oauth_state_is_signed_and_bound_to_owner():
    settings = _settings()
    owner = str(uuid.uuid4())
    state = google_calendar_mcp.make_oauth_state(owner, settings)
    assert google_calendar_mcp.verify_oauth_state(state, settings) == owner
    with pytest.raises(ValueError):
        google_calendar_mcp.verify_oauth_state(state + "x", settings)


def test_web_oauth_client_json_creates_calendar_authorization_url(tmp_path):
    credentials = tmp_path / "client_secret.json"
    credentials.write_text(json.dumps({"web": {
        "client_id": "example-client-id", "client_secret": "private-test-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}), encoding="utf-8")
    settings = Settings(_env_file=None, jwt_secret="test-only-secret-that-is-long-enough",
        google_calendar_client_secrets_file=credentials,
        google_calendar_redirect_uri="http://localhost:8000/interviews/google-calendar/callback")
    url = google_calendar_mcp.authorization_url(str(uuid.uuid4()), settings)
    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == ["example-client-id"]
    assert query["redirect_uri"] == [settings.google_calendar_redirect_uri]
    assert "https://www.googleapis.com/auth/calendar.events" in query["scope"][0]


def test_chat_interview_time_parses_clear_relative_and_iso_times():
    zone = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 10, 13, 8, tzinfo=ZoneInfo("UTC"))
    tomorrow = _chat_interview_time("tomorrow at 3 pm", "Asia/Dhaka", now)
    assert tomorrow is not None
    assert tomorrow.isoformat().startswith("2026-10-14T15:00:00+06:00")
    explicit = _chat_interview_time("2026-10-20 14:30", "Asia/Dhaka", now)
    assert explicit is not None and explicit.tzinfo == zone
    assert _chat_interview_time("next week sometime", "Asia/Dhaka", now) is None


def test_mcp_json_and_event_stream_responses_decode():
    payload = {"jsonrpc": "2.0", "id": 4, "result": {"structuredContent": {"id": "event-1"}}}
    assert google_calendar_mcp._decode_mcp_response(httpx.Response(200, json=payload)) == {"id": "event-1"}
    stream = httpx.Response(200, headers={"content-type": "text/event-stream"},
        text='event: message\ndata: {"jsonrpc":"2.0","id":4,"result":{"structuredContent":{"id":"event-2"}}}\n\n')
    assert google_calendar_mcp._decode_mcp_response(stream) == {"id": "event-2"}


def test_create_event_uses_candidate_attendee_and_private_visibility(monkeypatch):
    captured = {}

    async def fake_call(session, owner_id, settings, tool, arguments):
        captured.update(tool=tool, arguments=arguments)
        return {"id": "event-123"}

    monkeypatch.setattr(google_calendar_mcp, "call_tool", fake_call)
    start = google_calendar_mcp.datetime(2026, 10, 20, 9, tzinfo=google_calendar_mcp.timezone.utc)
    result = asyncio.run(google_calendar_mcp.create_event(None, uuid.uuid4(), _settings(),
        summary="Interview", description="Private link", candidate_email="person@example.com",
        start=start, end=start + google_calendar_mcp.timedelta(minutes=30), timezone_name="Asia/Dhaka"))
    assert result["id"] == "event-123"
    assert captured["tool"] == "create_event"
    assert captured["arguments"]["attendees"] == [{"email": "person@example.com"}]
    assert captured["arguments"]["visibility"] == "private"
    assert captured["arguments"]["notificationLevel"] == "ALL"


def test_update_event_reschedules_and_replaces_candidate_attendee(monkeypatch):
    captured = {}

    async def fake_call(session, owner_id, settings, tool, arguments):
        captured.update(tool=tool, arguments=arguments)
        return {"id": "event-123"}

    monkeypatch.setattr(google_calendar_mcp, "call_tool", fake_call)
    start = google_calendar_mcp.datetime(2026, 10, 20, 10, tzinfo=google_calendar_mcp.timezone.utc)
    asyncio.run(google_calendar_mcp.update_event(None, uuid.uuid4(), _settings(), event_id="event-123",
        start=start, end=start + google_calendar_mcp.timedelta(minutes=30), timezone_name="Asia/Dhaka",
        description="Updated private link", candidate_email="new@example.com",
        previous_candidate_email="old@example.com"))
    assert captured["tool"] == "update_event"
    assert captured["arguments"]["eventId"] == "event-123"
    assert captured["arguments"]["startTime"] == start.isoformat()
    assert captured["arguments"]["addedAttendees"] == [{"email": "new@example.com"}]
    assert captured["arguments"]["removedAttendeeEmails"] == ["old@example.com"]


def test_suggest_time_reads_mcp_time_slots(monkeypatch):
    async def fake_call(session, owner_id, settings, tool, arguments):
        assert tool == "suggest_time"
        assert arguments["attendeeEmails"] == ["recruiter@example.com"]
        return {"timeSlots": [{"start": {"dateTime": "2026-10-20T09:00:00+06:00"}}]}

    monkeypatch.setattr(google_calendar_mcp, "call_tool", fake_call)
    start = google_calendar_mcp.datetime(2026, 10, 20, 9, tzinfo=google_calendar_mcp.timezone.utc)
    slots = asyncio.run(google_calendar_mcp.suggest_time(None, uuid.uuid4(), _settings(),
        "recruiter@example.com", start, start + google_calendar_mcp.timedelta(hours=1),
        "Asia/Dhaka", 30))
    assert len(slots) == 1
    assert slots[0]["start"]["dateTime"].startswith("2026-10-20")


def test_calendar_api_fallback_creates_one_attendee_invitation_when_mcp_preview_denied(monkeypatch):
    calls = []

    async def denied(*args, **kwargs):
        raise RuntimeError("Access to this tool requires that your Google Cloud project is enrolled in the Google Workspace Developer Preview Program")

    async def api_request(session, owner_id, settings, method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {"id": "created-event"}

    monkeypatch.setattr(google_calendar_mcp, "call_tool", denied)
    monkeypatch.setattr(google_calendar_mcp, "_calendar_api_request", api_request)
    start = datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Dhaka"))
    result = asyncio.run(google_calendar_mcp.create_event(None, uuid.uuid4(), _settings(),
        summary="Interview", description="Private link", candidate_email="person@example.com",
        start=start, end=start + google_calendar_mcp.timedelta(minutes=30), timezone_name="Asia/Dhaka"))
    assert result == {"id": "created-event", "_delivery_method": "google_calendar_api"}
    assert calls[0][0:3] == ("POST", "/calendars/primary/events", {"sendUpdates": "all"})
    assert calls[0][3]["attendees"] == [{"email": "person@example.com"}]


def test_calendar_api_fallback_skips_busy_time_and_offers_free_slot(monkeypatch):
    async def denied(*args, **kwargs):
        raise RuntimeError("This project is not enrolled in the Google Workspace Developer Preview Program")

    async def api_request(session, owner_id, settings, method, path, *, params=None, body=None):
        assert method == "GET" and path == "/calendars/primary/events"
        return {"items": [{"start": {"dateTime": "2026-10-20T09:00:00+06:00"},
                           "end": {"dateTime": "2026-10-20T09:30:00+06:00"}}]}

    monkeypatch.setattr(google_calendar_mcp, "call_tool", denied)
    monkeypatch.setattr(google_calendar_mcp, "_calendar_api_request", api_request)
    start = datetime(2026, 10, 20, 9, tzinfo=ZoneInfo("Asia/Dhaka"))
    slots = asyncio.run(google_calendar_mcp.suggest_time(None, uuid.uuid4(), _settings(),
        "recruiter@example.com", start, start + google_calendar_mcp.timedelta(hours=2),
        "Asia/Dhaka", 30))
    assert slots[0]["start"]["dateTime"] == "2026-10-20T09:30:00+06:00"
