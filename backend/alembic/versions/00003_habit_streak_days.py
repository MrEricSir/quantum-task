"""add habit_streak_days table

Revision ID: 00003
Revises: 00002
Create Date: 2026-06-21 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "00003"
down_revision = "00002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "habit_streak_days",
        sa.Column("habit_id", sa.Integer,
                  sa.ForeignKey("habits.id", ondelete="CASCADE"),
                  primary_key=True, nullable=False),
        sa.Column("date",   sa.String,  primary_key=True, nullable=False),
        sa.Column("streak", sa.Integer, nullable=False),
    )
    op.create_index(
        "ix_habit_streak_days_habit_date",
        "habit_streak_days",
        ["habit_id", sa.text("date DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_habit_streak_days_habit_date", table_name="habit_streak_days")
    op.drop_table("habit_streak_days")
