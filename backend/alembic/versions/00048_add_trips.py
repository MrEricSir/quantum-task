"""Add trips table

Backs "trip mode": an open-ended travel window (start_date set, end_date null while
active) that streak.py's habit-streak accounting treats as a gap to skip rather than a
break, while HabitCompletion/health logging continue completely unaffected. See
PRODUCT_NOTES.md's "Trip mode" entry.

Revision ID: 00048
Revises: 00047
Create Date: 2026-09-01
"""
from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa

revision = "00048"
down_revision = "00047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "trips" not in inspect(bind).get_table_names():
        op.create_table(
            "trips",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("start_date", sa.String(), nullable=False),
            sa.Column("end_date", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("retrospective_sent", sa.Boolean(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_trips_id", "trips", ["id"])


def downgrade() -> None:
    op.drop_index("ix_trips_id", table_name="trips")
    op.drop_table("trips")
