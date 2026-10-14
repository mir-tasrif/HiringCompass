"""Track recruiter-approved interview transcript handoffs for F5.

Revision ID: 0015_f5_handoffs
Revises: 0014_google_calendar_mcp
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_f5_handoffs"
down_revision = "0014_google_calendar_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "f5_handoffs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("application_id", name="uq_f5_handoffs_application"),
    )


def downgrade() -> None:
    op.drop_table("f5_handoffs")
