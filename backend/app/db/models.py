"""Base domain models (WBS 2.1.2). Graph-specific result tables are added by later migrations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

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
    __table_args__ = (
        UniqueConstraint("public_code", name="uq_jobs_public_code"),
        Index("ix_jobs_owner_archived", "owner_id", "archived_at"),
    )

    title: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    active_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    public_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    next_candidate_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Immutable job profile + rubric snapshot; edits create a new version.
class JobVersion(Base, IdMixin, TimestampMixin):
    __tablename__ = "job_versions"
    __table_args__ = (UniqueConstraint("job_id", "version", name="uq_job_versions_job_version"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rubric: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# Delivery record for an approved job version on an external hiring platform.
class JobPosting(Base, IdMixin, TimestampMixin):
    __tablename__ = "job_postings"
    __table_args__ = (UniqueConstraint("job_id", "version", "platform", name="uq_job_postings_job_version_platform"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    platform: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
        UniqueConstraint("job_id", "candidate_number", name="uq_applications_job_candidate_number"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    job_version: Mapped[int] = mapped_column(Integer)
    candidate_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    stage: Mapped[str] = mapped_column(String(40), default="uploaded", server_default="uploaded")
    source: Mapped[str] = mapped_column(String(40), default="manual", server_default="manual")
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_from_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rejection_action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


# Durable recruiter-triggered batch for parsing/integrity or F1 ranking.
class CvBatch(Base, IdMixin, TimestampMixin):
    __tablename__ = "cv_batches"
    __table_args__ = (Index("ix_cv_batches_owner_created", "owner_id", "created_at"),)

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    thread_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_threads.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="queued", server_default="queued")
    total: Mapped[int] = mapped_column(Integer)
    completion_notified: Mapped[bool] = mapped_column(default=False, server_default=text("false"))


# One independently retryable CV task in a batch.
class CvBatchItem(Base, IdMixin, TimestampMixin):
    __tablename__ = "cv_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "application_id", name="uq_cv_batch_items_batch_application"),
        Index("ix_cv_batch_items_status_created", "status", "created_at"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cv_batches.id", ondelete="CASCADE"))
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notified: Mapped[bool] = mapped_column(default=False, server_default=text("false"))


# Parsed candidate evidence and explainable F11 signals; retained for recruiter review/audit.
class CandidateProfile(Base, IdMixin, TimestampMixin):
    __tablename__ = "candidate_profiles"
    __table_args__ = (UniqueConstraint("application_id", name="uq_candidate_profiles_application"),)

    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    extracted_text: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(20))
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    integrity_verdict: Mapped[str] = mapped_column(String(30))
    integrity_signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    integrity_policy_version: Mapped[str] = mapped_column(String(40))


# F1 evidence-based requirement match and per-job ranking result.
class CandidateScreening(Base, IdMixin, TimestampMixin):
    __tablename__ = "candidate_screenings"
    __table_args__ = (UniqueConstraint("application_id", name="uq_candidate_screenings_application"),)

    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    job_version: Mapped[int] = mapped_column(Integer)
    suitability_score: Mapped[int] = mapped_column(Integer)
    mandatory_pass: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    requirements: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)


# Append-only stage and rejection history for each application.
class ApplicationEvent(Base, IdMixin):
    __tablename__ = "application_events"
    __table_args__ = (Index("ix_application_events_application_created", "application_id", "created_at"),)

    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(20))
    from_stage: Mapped[str] = mapped_column(String(40))
    to_stage: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
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



# A document ingested into a knowledge namespace (company hiring policy or technical reference).
class KnowledgeSource(Base, IdMixin, TimestampMixin):
    __tablename__ = "knowledge_sources"
    __table_args__ = (UniqueConstraint("namespace", "checksum", name="uq_knowledge_sources_ns_checksum"),)

    namespace: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    source_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    checksum: Mapped[str] = mapped_column(String(64))


# One embedded passage of a knowledge source; namespaces are never mixed at query time.
class KnowledgeChunk(Base, IdMixin, TimestampMixin):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_namespace", "namespace"),
        Index("ix_knowledge_chunks_source", "source_id"),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"))
    namespace: Mapped[str] = mapped_column(String(40))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(768))




# One assistant conversation owned by an interviewer; tracks the linked job and the current LangGraph run.
class ChatThread(Base, IdMixin, TimestampMixin):
    __tablename__ = "chat_threads"
    __table_args__ = (Index("ix_chat_threads_owner_updated", "owner_id", "updated_at"),)

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), default="New job chat", server_default="New job chat")
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    state: Mapped[str] = mapped_column(String(30), default="interviewing", server_default="interviewing")
    status: Mapped[str] = mapped_column(String(20), default="idle", server_default="idle")
    run_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active_run: Mapped[str | None] = mapped_column(String(120), nullable=True)


# One message in a thread; `payload` carries structured content (question options, draft card, ...).
class ChatMessage(Base, IdMixin):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_thread_created", "thread_id", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
