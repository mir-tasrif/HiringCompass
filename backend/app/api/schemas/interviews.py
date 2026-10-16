"""Request and response contracts for scheduling, joining, and interview transcript endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ScheduleBody(BaseModel):
    application_id: uuid.UUID
    candidate_email: str = Field(min_length=5, max_length=255)
    scheduled_at: datetime
    timezone_name: str = Field(min_length=1, max_length=80)
    duration_minutes: int = Field(default=30, ge=15, le=180)


class RescheduleBody(BaseModel):
    candidate_email: str = Field(min_length=5, max_length=255)
    scheduled_at: datetime
    timezone_name: str = Field(min_length=1, max_length=80)
    duration_minutes: int | None = Field(default=None, ge=15, le=180)


class InterviewOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    job_id: uuid.UUID
    job_code: str
    job_title: str
    candidate_name: str
    candidate_number: int
    candidate_email: str
    scheduled_at: datetime
    timezone_name: str
    duration_minutes: int = 30
    calendar_sync_status: str = "not_connected"
    calendar_sync_error: str | None = None
    invitation_delivery: str | None = None
    status: str
    invitation_sent_at: datetime | None
    candidate_waiting_at: datetime | None
    candidate_admitted_at: datetime | None
    candidate_joined_at: datetime | None
    interviewer_joined_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    questions: list[dict]
    job_description: dict = {}
    candidate_profile: dict | None = None
    f1: dict | None = None
    f2: dict | None = None
    transcript_turn_count: int = 0
    f5_ready: bool = False


class GuestOut(BaseModel):
    session_id: uuid.UUID
    job_title: str
    candidate_name: str
    scheduled_at: datetime
    timezone_name: str
    status: str
    admitted: bool
    started: bool


class JoinOut(BaseModel):
    url: str
    token: str
    room: str
    role: str
    stt_provider: str = "groq"
    stt_token: str | None = None


class TurnOut(BaseModel):
    id: uuid.UUID
    role: str
    started_at: datetime
    ended_at: datetime
    text: str


class ConsentBody(BaseModel):
    consent: bool


class QuestionsBody(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=20)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class CalendarConnectOut(BaseModel):
    connected: bool
    account_email: str | None = None
    authorization_url: str | None = None


