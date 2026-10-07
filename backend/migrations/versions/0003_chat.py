"""chat: chat_threads and chat_messages for the AI assistant

Revision ID: 0003_chat
Revises: 0002_knowledge_base
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003_chat"
down_revision = "0002_knowledge_base"
branch_labels = None
depends_on = None


# Primary key column with a database-side UUID default.
def _id() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


# Create both chat tables and their indexes.
def upgrade() -> None:
    op.create_table(
        "chat_threads", _id(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("owner_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), server_default="New job chat", nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("state", sa.String(30), server_default="interviewing", nullable=False),
        sa.Column("status", sa.String(20), server_default="idle", nullable=False),
        sa.Column("run_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active_run", sa.String(120), nullable=True),
    )
    op.create_index("ix_chat_threads_owner_updated", "chat_threads", ["owner_id", "updated_at"])
    op.create_table(
        "chat_messages", _id(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("thread_id", pg.UUID(as_uuid=True), sa.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=True),
    )
    op.create_index("ix_chat_messages_thread_created", "chat_messages", ["thread_id", "created_at"])


# Drop both chat tables.
def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_threads")