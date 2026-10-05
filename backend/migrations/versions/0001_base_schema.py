"""base schema: users, jobs, candidates, applications, documents, work_items, approvals, audit_events

Revision ID: 0001_base_schema
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_base_schema"
down_revision = None
branch_labels = None
depends_on = None


# Primary key column with a database-side UUID default.
def _id() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


# Created/updated timestamp columns shared by every table.
def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


# Create all base tables in dependency order.
def upgrade() -> None:
    op.create_table(
        "users", _id(), *_ts(),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "jobs", _id(), *_ts(),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("owner_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("active_version", sa.Integer(), nullable=True),
    )
    op.create_table(
        "job_versions", _id(), *_ts(),
        sa.Column("job_id", pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("profile", pg.JSONB(), nullable=False),
        sa.Column("rubric", pg.JSONB(), nullable=True),
        sa.UniqueConstraint("job_id", "version", name="uq_job_versions_job_version"),
    )
    op.create_table(
        "candidates", _id(), *_ts(),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
    )
    op.create_table(
        "applications", _id(), *_ts(),
        sa.Column("candidate_id", pg.UUID(as_uuid=True), sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("job_version", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(40), server_default="uploaded", nullable=False),
        sa.UniqueConstraint("candidate_id", "job_id", name="uq_applications_candidate_job"),
    )
    op.create_index("ix_applications_job_stage", "applications", ["job_id", "stage"])
    op.create_table(
        "documents", _id(), *_ts(),
        sa.Column("application_id", pg.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(30), server_default="stored", nullable=False),
        sa.UniqueConstraint("application_id", "version", name="uq_documents_application_version"),
    )
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])
    op.create_table(
        "work_items", _id(), *_ts(),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("thread_id", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(30), server_default="ready", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(80), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_category", sa.String(80), nullable=True),
        sa.Column("payload", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_work_items_idempotency_key"),
    )
    op.create_index("ix_work_items_status_retry", "work_items", ["status", "next_retry_at"])
    op.create_table(
        "approvals", _id(), *_ts(),
        sa.Column("gate", sa.String(40), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("application_id", pg.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=True),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("allowed_options", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("decision_option", sa.String(80), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_approvals_status_gate", "approvals", ["status", "gate"])
    op.create_table(
        "audit_events", _id(), *_ts(),
        sa.Column("actor_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("detail", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )


# Drop all base tables in reverse dependency order.
def downgrade() -> None:
    for table in ("audit_events", "approvals", "work_items", "documents", "applications", "candidates", "job_versions", "jobs", "users"):
        op.drop_table(table)