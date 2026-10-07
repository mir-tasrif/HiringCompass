"""Track candidate sources and preserve rejected CVs with a decision log.

Revision ID: 0006_candidate_rejections
Revises: 0005_job_codes_cv_labels
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0006_candidate_rejections"
down_revision = "0005_job_codes_cv_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("source", sa.String(40), server_default="manual", nullable=False))
    op.add_column("applications", sa.Column("rejection_reason", sa.Text(), nullable=True))
    op.add_column("applications", sa.Column("rejected_from_stage", sa.String(40), nullable=True))
    op.add_column("applications", sa.Column("rejection_action", sa.String(20), nullable=True))
    op.add_column("applications", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("applications", sa.Column("rejected_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_applications_rejected_by_users", "applications", "users", ["rejected_by"], ["id"])

    op.create_table(
        "application_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("from_stage", sa.String(40), nullable=False),
        sa.Column("to_stage", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_application_events_application_created", "application_events", ["application_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_application_events_application_created", table_name="application_events")
    op.drop_table("application_events")
    op.drop_constraint("fk_applications_rejected_by_users", "applications", type_="foreignkey")
    op.drop_column("applications", "rejected_by")
    op.drop_column("applications", "rejected_at")
    op.drop_column("applications", "rejection_action")
    op.drop_column("applications", "rejected_from_stage")
    op.drop_column("applications", "rejection_reason")
    op.drop_column("applications", "source")
