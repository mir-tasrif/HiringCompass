"""Allow partially met mandatory requirements to pass the F1 ranking gate.

Revision ID: 0009_partial_mandatory_ranking
Revises: 0008_cv_processing
"""

from alembic import op

revision = "0009_partial_mandatory_ranking"
down_revision = "0008_cv_processing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE candidate_screenings
        SET mandatory_pass = NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(requirements) AS requirement(value)
            WHERE value->>'kind' = 'mandatory'
              AND COALESCE(value->>'status', 'needs_review') NOT IN ('met', 'partially_met')
        )
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE candidate_screenings
        SET mandatory_pass = NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(requirements) AS requirement(value)
            WHERE value->>'kind' = 'mandatory'
              AND COALESCE(value->>'status', 'needs_review') <> 'met'
        )
    """)
