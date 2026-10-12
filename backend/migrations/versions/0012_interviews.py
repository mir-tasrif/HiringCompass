"""Interview scheduling, access, and persisted transcript turns.

Revision ID: 0012_interviews
Revises: 0011_f2_scoring
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_interviews"
down_revision = "0011_f2_scoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Populate contact addresses for CVs parsed before email extraction was added.
    op.execute(r"""
        UPDATE candidates AS c
        SET email = lower((regexp_match(left(cp.extracted_text, 30000),
            '([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})', 'i'))[1])
        FROM applications AS a
        JOIN candidate_profiles AS cp ON cp.application_id = a.id
        WHERE a.candidate_id = c.id AND c.email IS NULL
          AND cp.extracted_text ~* '[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}'
    """)
    op.create_table(
        "interview_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("candidate_email", sa.String(255), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="scheduled"),
        sa.Column("invitation_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invitation_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invitation_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_waiting_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interviewer_consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_admitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interviewer_joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("questions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_index("ix_interview_sessions_owner_schedule", "interview_sessions", ["owner_id", "scheduled_at"])
    op.create_table(
        "interview_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("chunk_id", sa.String(80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False, server_default="groq_whisper"),
        sa.UniqueConstraint("session_id", "role", "chunk_id", name="uq_interview_turn_chunk"),
    )
    op.create_index("ix_interview_turns_session_time", "interview_turns", ["session_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_interview_turns_session_time", table_name="interview_turns")
    op.drop_table("interview_turns")
    op.drop_index("ix_interview_sessions_owner_schedule", table_name="interview_sessions")
    op.drop_table("interview_sessions")
