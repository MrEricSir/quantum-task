"""Enforce at most one active experiment per week (partial unique index)

Revision ID: 00015
Revises: 00014
Create Date: 2026-07-13
"""
from alembic import op

revision = '00015'
down_revision = '00014'
branch_labels = None
depends_on = None


def upgrade():
    # SQLite supports partial (WHERE-clause) unique indexes.
    # This prevents concurrent requests from creating duplicate active experiments.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_health_experiments_active_week "
        "ON health_experiments(week) WHERE status = 'active'"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_health_experiments_active_week")
