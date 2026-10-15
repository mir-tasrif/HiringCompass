"""Persist versioned F5 analysis batches, transcript snapshots, and review decisions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017_f5_analysis"
down_revision = "0016_cv_prompt_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("f5_batches",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), sa.ForeignKey("chat_threads.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(30), server_default="queued", nullable=False))
    op.create_table("f5_runs",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("batch_id", UUID(as_uuid=True), sa.ForeignKey("f5_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interview_id", UUID(as_uuid=True), sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_version", sa.Integer(), nullable=False),
        sa.Column("transcript", JSONB(), nullable=False),
        sa.Column("segment_overrides", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("corrections", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("result", JSONB()),
        sa.Column("status", sa.String(30), server_default="queued", nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("review_status", sa.String(30), server_default="pending", nullable=False),
        sa.Column("reviewed_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_reason", sa.Text()))
    op.create_index("ix_f5_runs_status_created", "f5_runs", ["status", "created_at"])
    op.create_index("ix_f5_runs_application_created", "f5_runs", ["application_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_f5_runs_application_created", table_name="f5_runs")
    op.drop_index("ix_f5_runs_status_created", table_name="f5_runs")
    op.drop_table("f5_runs")
    op.drop_table("f5_batches")
