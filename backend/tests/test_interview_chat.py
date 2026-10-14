"""Chat scheduling rules that must remain stable across draft and confirmation turns."""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import interview_chat


ZONE = ZoneInfo("Asia/Dhaka")


def test_natural_time_and_office_hours():
    now = datetime(2026, 10, 14, 8, 0, tzinfo=ZONE)
    assert interview_chat.parse_interview_time("today 10 am", "Asia/Dhaka", now) == now.replace(hour=10)
    assert interview_chat.parse_interview_time("today 10:20", "Asia/Dhaka", now) == now.replace(hour=10, minute=20)
    assert interview_chat.parse_interview_time("2026-10-15 3 pm", "Asia/Dhaka", now) == datetime(2026, 10, 15, 15, tzinfo=ZONE)
    assert interview_chat.office_cursor(datetime(2026, 10, 16, 16, 45, tzinfo=ZONE)) == datetime(2026, 10, 19, 9, tzinfo=ZONE)


def test_completed_candidate_is_not_presented_as_ready_to_schedule():
    job = SimpleNamespace(id=uuid.uuid4(), public_code="JD109")
    text, _ = interview_chat.roster_reply(job, [{"application_id": "1", "label": "candidate003",
        "name": "Candidate Three", "email": "three@example.com", "interview_id": "ended-id",
        "interview_status": "ended", "scheduled_at": "2026-10-14T09:40:00+06:00",
        "invitation_sent": True}])
    assert "candidate003" in text and "interview completed" in text
    assert "ready to schedule" not in text


@pytest.mark.asyncio
async def test_batch_plan_skips_existing_interview_and_uses_ten_minute_gap(monkeypatch):
    roster = [
        {"application_id": "1", "label": "candidate001", "name": "A", "email": "a@example.com", "interview_id": "existing"},
        {"application_id": "2", "label": "candidate002", "name": "B", "email": "b@example.com", "interview_id": None},
        {"application_id": "3", "label": "candidate003", "name": "C", "email": "c@example.com", "interview_id": None},
    ]

    async def fake_roster(*_):
        return roster

    async def fake_slot(_session, _settings, _owner_id, _email, cursor):
        return interview_chat.office_cursor(cursor)

    monkeypatch.setattr(interview_chat, "selected_roster", fake_roster)
    monkeypatch.setattr(interview_chat, "_free_slot", fake_slot)
    requested = datetime.now(ZONE) + timedelta(days=7)
    requested = requested.replace(hour=9, minute=0, second=0, microsecond=0)
    _, payload = await interview_chat._plan(object(), object(), uuid.uuid4(),
        SimpleNamespace(id=uuid.uuid4(), public_code="JD109"), requested)
    assert [entry["label"] for entry in payload["entries"]] == ["candidate002", "candidate003"]
    first, second = (datetime.fromisoformat(entry["start"]) for entry in payload["entries"])
    assert second - first == timedelta(minutes=40)
    assert all(entry["start"].startswith(str(first.year)) for entry in payload["entries"])


@pytest.mark.asyncio
async def test_group_request_keeps_its_context_but_named_candidate_uses_single_flow(monkeypatch):
    job_id, owner_id = uuid.uuid4(), uuid.uuid4()
    job = SimpleNamespace(id=job_id, public_code="JD109")

    class Session:
        async def scalar(self, _statement):
            return job

    async def fake_plan(_session, _settings, _owner_id, _job, requested):
        return "Preview", {"type": "interview_batch", "state": "preview", "job_id": str(job_id),
                           "requested_start": requested.isoformat(), "entries": []}

    monkeypatch.setattr(interview_chat, "_plan", fake_plan)
    session = Session()
    text, pending = await interview_chat.handle_interview_chat(session, object(), owner_id, job_id,
        "schedule the rest of the selected candidates", {})
    assert "start time" in text and pending["state"] == "need_time"
    text, preview = await interview_chat.handle_interview_chat(session, object(), owner_id, job_id,
        "start from today 10 am", pending)
    assert text == "Preview" and preview["state"] == "preview"
    assert await interview_chat.handle_interview_chat(session, object(), owner_id, job_id,
        "schedule candidate003", pending) is None
