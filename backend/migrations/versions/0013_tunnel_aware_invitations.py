"""Track the public base URL used by each interview invitation.

Revision ID: 0013_tunnel_aware_invitations
Revises: 0012_interviews
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_tunnel_aware_invitations"
down_revision = "0012_interviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interview_sessions", sa.Column("invitation_base_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_sessions", "invitation_base_url")
