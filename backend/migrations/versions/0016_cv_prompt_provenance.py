"""Store the instructions used for newly generated CV profiles and scores.

Revision ID: 0016_cv_prompt_provenance
Revises: 0015_f5_handoffs
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_cv_prompt_provenance"
down_revision = "0015_f5_handoffs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historic results cannot be assigned a prompt version reliably.
    for table, prefix in (("candidate_profiles", "profile_prompt"),
                          ("candidate_screenings", "prompt"),
                          ("candidate_scores", "prompt")):
        op.add_column(table, sa.Column(f"{prefix}_key", sa.String(length=80), nullable=True))
        op.add_column(table, sa.Column(f"{prefix}_version", sa.String(length=40), nullable=True))


def downgrade() -> None:
    for table, prefix in (("candidate_scores", "prompt"),
                          ("candidate_screenings", "prompt"),
                          ("candidate_profiles", "profile_prompt")):
        op.drop_column(table, f"{prefix}_version")
        op.drop_column(table, f"{prefix}_key")
