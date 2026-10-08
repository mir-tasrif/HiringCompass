"""Support reversible job archiving without deleting related records.

Revision ID: 0007_job_archiving
Revises: 0006_candidate_rejections
"""

import sqlalchemy as sa
from alembic import op


revision = "0007_job_archiving"
down_revision = "0006_candidate_rejections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_owner_archived", "jobs", ["owner_id", "archived_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_owner_archived", table_name="jobs")
    op.drop_column("jobs", "archived_at")
