"""Track external delivery of approved job versions.

Revision ID: 0004_job_postings
Revises: 0003_chat
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004_job_postings"
down_revision = "0003_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_postings",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("external_id", sa.String(120), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("job_id", "version", "platform", name="uq_job_postings_job_version_platform"),
    )


def downgrade() -> None:
    op.drop_table("job_postings")
