"""Base domain models (WBS 2.1.2). Graph-specific result tables are added by later migrations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


# Interviewer or candidate account (two roles only).
class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


# A hiring job owned by an interviewer; points at its active version.
class Job(Base, IdMixin, TimestampMixin):
    __tablename__ = "jobs"

    title: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    active_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


# Immutable job profile + rubric snapshot; edits create a new version.
class JobVersion(Base, IdMixin, TimestampMixin):
    __tablename__ = "job_versions"
    __table_args__ = (UniqueConstraint("job_id", "version", name="uq_job_versions_job_version"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rubric: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# Person who applied; holds the only contact data (deleted on request, N19).
class Candidate(Base, IdMixin, TimestampMixin):
    __tablename__ = "candidates"

    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)


# One candidate's application to one job; all results hang off this record.
class Application(Base, IdMixin, TimestampMixin):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("candidate_id", "job_id", name="uq_applications_candidate_job"),
        Index("ix_applications_job_stage", "job_id", "stage"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    job_version: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(40), default="uploaded", server_default="uploaded")


# Stored CV file metadata; replacement creates a new version.
class Document(Base, IdMixin, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_documents_application_version"),
        Index("ix_documents_content_hash", "content_hash"),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    file_path: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30), default="stored", server_default="stored")


# Durable work item claimed by the worker with a time-limited lease.
class WorkItem(Base, IdMixin, TimestampMixin):
    __tablename__ = "work_items"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_work_items_idempotency_key"),
        Index("ix_work_items_status_retry", "status", "next_retry_at"),
    )

    kind: Mapped[str] = mapped_column(String(40))
    thread_id: Mapped[str] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="ready", server_default="ready")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


# Persisted human-review gate with its eventual decision.
class Approval(Base, IdMixin, TimestampMixin):
    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_status_gate", "status", "gate"),)

    gate: Mapped[str] = mapped_column(String(40))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    evidence_summary: Mapped[str] = mapped_column(Text)
    allowed_options: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    decision_option: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Append-only audit trail; stores IDs only, never personal data.
class AuditEvent(Base, IdMixin, TimestampMixin):
    __tablename__ = "audit_events"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))