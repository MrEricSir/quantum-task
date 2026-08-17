"""add matched-baseline and calorie-confound fields for workout experiments

Same concept already shipped for food-elimination experiments, applied to
workout-routine ones: weight_baseline/fat_baseline get overridden at outcome
time to the mean across weeks this workout type was actually being logged
(workout_baseline_weeks_n of them), and workout_baseline_avg_calories/
workout_experiment_avg_calories give the same one-variable calorie confound
check. Also fixes a real bug -- experimentVerdict() previously showed the
generic weight/fat verdict for workout experiments unconditionally, never
actually gating on whether the workout_p significance test (or the target)
showed the routine change had actually happened.

Revision ID: 00036
Revises: 00035
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "00036"
down_revision = "00035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("workout_baseline_weeks_n", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("workout_baseline_avg_calories", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("workout_experiment_avg_calories", sa.Float, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.drop_column("workout_experiment_avg_calories")
        batch_op.drop_column("workout_baseline_avg_calories")
        batch_op.drop_column("workout_baseline_weeks_n")
