"""Request and response contracts for candidate CV and batch endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CandidateEventOut(BaseModel):
    action: str
    from_stage: str
    to_stage: str
    reason: str
    created_at: datetime


class CandidateCvOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    job_code: str
    job_title: str
    job_version: int
    candidate_number: int
    candidate_name: str
    candidate_email: str | None = None
    source: str
    original_filename: str | None
    converted_from_docx: bool
    size_bytes: int
    uploaded_at: datetime
    stage: str
    rejection_action: str | None = None
    rejection_reason: str | None = None
    rejected_from_stage: str | None = None
    rejected_at: datetime | None = None
    events: list[CandidateEventOut] = Field(default_factory=list)
    parsed_profile: dict | None = None
    integrity_verdict: str | None = None
    integrity_signals: list[dict] = Field(default_factory=list)
    extraction_method: str | None = None
    profile_prompt_key: str | None = None
    profile_prompt_version: str | None = None
    screening: dict | None = None
    scoring: dict | None = None
    integrity_task_id: uuid.UUID | None = None
    integrity_task_status: str | None = None
    integrity_task_error: str | None = None
    ranking_task_id: uuid.UUID | None = None
    ranking_task_status: str | None = None
    ranking_task_error: str | None = None
    ranking_task_next_attempt_at: datetime | None = None
    scoring_task_id: uuid.UUID | None = None
    scoring_task_status: str | None = None
    scoring_task_error: str | None = None
    scoring_task_next_attempt_at: datetime | None = None
    f5_interview_id: uuid.UUID | None = None
    f5_interview_ended_at: datetime | None = None


class CandidateActionBody(BaseModel):
    application_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    action: Literal["delete", "reject", "permanent_delete"]
    reason: str = Field(min_length=1, max_length=2000)


class CandidateActionOut(BaseModel):
    rejected_ids: list[uuid.UUID]


class StartBatchBody(BaseModel):
    application_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class StartScoringBody(StartBatchBody):
    weights: dict[str, int]
    rescore: bool = False


class ScoringWeightsOut(BaseModel):
    job_id: uuid.UUID
    job_code: str
    weights: dict[str, int]
    policy_version: int | None


class BatchItemOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    status: str
    attempts: int
    error: str | None
    next_attempt_at: datetime | None
    candidate_label: str | None = None


class BatchOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    kind: str
    status: str
    total: int
    completed: int
    failed: int
    waiting_review: int
    rejected: int
    cancelled: int
    items: list[BatchItemOut]


class StartBatchesOut(BaseModel):
    batches: list[BatchOut]


class RetryOut(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    status: str


class ReviewDecisionBody(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=2000)


class ReviewDecisionOut(BaseModel):
    application_id: uuid.UUID
    stage: str
    decision: str


