"""Add retrospective_skipped to trips

Distinguishes "the retrospective was deliberately skipped because the trip ended too soon
after it started to be real (an accidental toggle)" from retrospective_sent=False, which
means "still owed, the scheduler backstop should retry" -- see trip/router.py's
MIN_TRIP_DURATION_MINUTES and telegram/scheduler.py's check_trip_retrospective.

Revision ID: 00049
Revises: 00048
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa

revision = "00049"
down_revision = "00048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trips") as batch_op:
        batch_op.add_column(sa.Column("retrospective_skipped", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trips") as batch_op:
        batch_op.drop_column("retrospective_skipped")
