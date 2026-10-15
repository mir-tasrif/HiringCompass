"""Persist Google Calendar MCP connection and interview event sync state.

Revision ID: 0014_google_calendar_mcp
Revises: 0013_tunnel_aware_invitations
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_google_calendar_mcp"
down_revision = "0013_tunnel_aware_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interview_sessions", sa.Column("duration_minutes", sa.Integer(), server_default="30", nullable=False))
    op.add_column("interview_sessions", sa.Column("calendar_event_id", sa.String(length=255), nullable=True))
    op.add_column("interview_sessions", sa.Column("calendar_sync_status", sa.String(length=30), server_default="not_connected", nullable=False))
    op.add_column("interview_sessions", sa.Column("calendar_sync_error", sa.Text(), nullable=True))
    op.add_column("interview_sessions", sa.Column("invitation_delivery", sa.String(length=30), nullable=True))
    op.create_table(
        "google_calendar_connections",
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("account_email", sa.String(length=255), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", name="uq_google_calendar_connections_owner"),
    )


def downgrade() -> None:
    op.drop_table("google_calendar_connections")
    op.drop_column("interview_sessions", "invitation_delivery")
    op.drop_column("interview_sessions", "calendar_sync_error")
    op.drop_column("interview_sessions", "calendar_sync_status")
    op.drop_column("interview_sessions", "calendar_event_id")
    op.drop_column("interview_sessions", "duration_minutes")
