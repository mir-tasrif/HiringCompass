"""Persist CV retry scheduling and explicit auto-ranking intent.

Revision ID: 0010_cv_retry_schedule
Revises: 0009_partial_mandatory_ranking
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_cv_retry_schedule"
down_revision = "0009_partial_mandatory_ranking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cv_batch_items", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cv_batches", sa.Column("auto_start_ranking", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_index("ix_cv_batch_items_next_attempt", "cv_batch_items", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_cv_batch_items_next_attempt", table_name="cv_batch_items")
    op.drop_column("cv_batches", "auto_start_ranking")
    op.drop_column("cv_batch_items", "next_attempt_at")
