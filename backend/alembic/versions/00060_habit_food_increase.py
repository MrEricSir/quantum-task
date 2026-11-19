"""Add food_increase_name/food_increase_target to habits and health_experiments

Backs auto-tracking for a food-increase experiment habit (e.g. "eat a protein bar 4
times this week"), the positive-presence mirror of food_avoid_name/food_avoid_target
(see routers/correlations.py's check_habit_for_food). Before this, "eat more of X"
experiments had no structured field at all -- they fell into the generic
routine_type="habit" catch-all, which is manual-checkbox-only, unlike a workout-routine
habit (auto-checked from WorkoutEntry) or a food-elimination habit (auto-checked from
the absence of a matching FoodEntry). This gave "eat a protein bar 4 times" habits no
way to auto-check off from a matching food log entry at all.

The health_experiments columns are a separate pair from the existing food_name/
food_target_frequency (not a reuse) -- those are reduction-direction only, and the
increase case has no baseline/outcome logic of its own (scored via the generic
habit_completion_rate path instead, see models.py's HealthExperiment docstring).

Revision ID: 00060
Revises: 00059
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "00060"
down_revision = "00059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("habits") as batch_op:
        batch_op.add_column(sa.Column("food_increase_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("food_increase_target", sa.Float(), nullable=True))
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("food_increase_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("food_increase_target", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.drop_column("food_increase_target")
        batch_op.drop_column("food_increase_name")
    with op.batch_alter_table("habits") as batch_op:
        batch_op.drop_column("food_increase_target")
        batch_op.drop_column("food_increase_name")
