"""Persist CV extraction, integrity checks, review, and ranking batches.

Revision ID: 0008_cv_processing
Revises: 0007_job_archiving
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0008_cv_processing"
down_revision = "0007_job_archiving"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_threads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), server_default="queued", nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("completion_notified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cv_batches_owner_created", "cv_batches", ["owner_id", "created_at"])
    op.create_table(
        "cv_batch_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cv_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "application_id", name="uq_cv_batch_items_batch_application"),
    )
    op.create_index("ix_cv_batch_items_status_created", "cv_batch_items", ["status", "created_at"])
    op.create_table(
        "candidate_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.String(20), nullable=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("integrity_verdict", sa.String(30), nullable=False),
        sa.Column("integrity_signals", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("integrity_policy_version", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", name="uq_candidate_profiles_application"),
    )
    op.create_table(
        "candidate_screenings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_version", sa.Integer(), nullable=False),
        sa.Column("suitability_score", sa.Integer(), nullable=False),
        sa.Column("mandatory_pass", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("requirements", postgresql.JSONB(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", name="uq_candidate_screenings_application"),
    )


def downgrade() -> None:
    op.drop_table("candidate_screenings")
    op.drop_table("candidate_profiles")
    op.drop_index("ix_cv_batch_items_status_created", table_name="cv_batch_items")
    op.drop_table("cv_batch_items")
    op.drop_index("ix_cv_batches_owner_created", table_name="cv_batches")
    op.drop_table("cv_batches")
