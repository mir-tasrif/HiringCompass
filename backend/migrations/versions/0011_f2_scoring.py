"""Versioned F2 policies and auditable candidate scores.

Revision ID: 0011_f2_scoring
Revises: 0010_cv_retry_schedule
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_f2_scoring"
down_revision = "0010_cv_retry_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "f2_scoring_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("weights", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "version", name="uq_f2_policy_job_version"),
    )
    op.add_column("cv_batches", sa.Column("scoring_policy_id", postgresql.UUID(as_uuid=True),
                                          sa.ForeignKey("f2_scoring_policies.id", ondelete="SET NULL"), nullable=True))
    op.create_table(
        "candidate_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cv_batch_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("f2_scoring_policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("weights", postgresql.JSONB(), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(), nullable=False),
        sa.Column("aggregate_score", sa.Float(), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("scoring_version", sa.String(30), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_item_id", name="uq_candidate_scores_batch_item"),
    )
    op.create_index("ix_candidate_scores_application_created", "candidate_scores", ["application_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_candidate_scores_application_created", table_name="candidate_scores")
    op.drop_table("candidate_scores")
    op.drop_column("cv_batches", "scoring_policy_id")
    op.drop_table("f2_scoring_policies")
