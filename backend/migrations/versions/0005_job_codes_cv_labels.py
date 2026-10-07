"""Add readable job codes and per-job candidate labels.

Revision ID: 0005_job_codes_cv_labels
Revises: 0004_job_postings
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_job_codes_cv_labels"
down_revision = "0004_job_postings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE job_code_seq START WITH 101")
    op.add_column("jobs", sa.Column("public_code", sa.String(20), nullable=True))
    op.add_column("jobs", sa.Column("next_candidate_number", sa.Integer(), server_default="1", nullable=False))
    op.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS n
            FROM jobs WHERE active_version IS NOT NULL
        )
        UPDATE jobs SET public_code = 'JD' || (100 + ranked.n)::text
        FROM ranked WHERE jobs.id = ranked.id
    """)
    op.execute("""
        SELECT setval('job_code_seq', COALESCE(
            (SELECT max(substring(public_code FROM 3)::integer) FROM jobs WHERE public_code IS NOT NULL), 100
        ), true)
    """)
    op.create_unique_constraint("uq_jobs_public_code", "jobs", ["public_code"])

    op.add_column("applications", sa.Column("candidate_number", sa.Integer(), nullable=True))
    op.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY job_id ORDER BY created_at, id) AS n
            FROM applications
        )
        UPDATE applications SET candidate_number = ranked.n
        FROM ranked WHERE applications.id = ranked.id
    """)
    op.alter_column("applications", "candidate_number", nullable=False, server_default="1")
    op.create_unique_constraint("uq_applications_job_candidate_number", "applications", ["job_id", "candidate_number"])
    op.execute("""
        UPDATE jobs SET next_candidate_number = COALESCE(
            (SELECT max(candidate_number) + 1 FROM applications WHERE applications.job_id = jobs.id), 1
        )
    """)
    op.add_column("documents", sa.Column("original_filename", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "original_filename")
    op.drop_constraint("uq_applications_job_candidate_number", "applications", type_="unique")
    op.drop_column("applications", "candidate_number")
    op.drop_constraint("uq_jobs_public_code", "jobs", type_="unique")
    op.drop_column("jobs", "next_candidate_number")
    op.drop_column("jobs", "public_code")
    op.execute("DROP SEQUENCE job_code_seq")
