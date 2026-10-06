"""knowledge base: knowledge_sources and knowledge_chunks (pgvector, 768 dims, HNSW cosine index)

Revision ID: 0002_knowledge_base
Revises: 0001_base_schema
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0002_knowledge_base"
down_revision = "0001_base_schema"
branch_labels = None
depends_on = None


# Primary key column with a database-side UUID default.
def _id() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


# Created/updated timestamp columns.
def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


# Create the vector extension (idempotent) and both knowledge tables with their indexes.
def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_sources", _id(), *_ts(),
        sa.Column("namespace", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source_uri", sa.String(500), nullable=True),
        sa.Column("version", sa.String(80), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.UniqueConstraint("namespace", "checksum", name="uq_knowledge_sources_ns_checksum"),
    )
    op.create_table(
        "knowledge_chunks", _id(), *_ts(),
        sa.Column("source_id", pg.UUID(as_uuid=True), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("namespace", sa.String(40), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
    )
    op.create_index("ix_knowledge_chunks_namespace", "knowledge_chunks", ["namespace"])
    op.create_index("ix_knowledge_chunks_source", "knowledge_chunks", ["source_id"])
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw", "knowledge_chunks", ["embedding"],
        postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
    )


# Drop both knowledge tables (the vector extension is left installed).
def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_sources")