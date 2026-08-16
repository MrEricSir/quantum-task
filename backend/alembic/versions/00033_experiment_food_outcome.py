"""add food-elimination fields to health_experiments

Lets a weekly health experiment target a regularly-eaten food (e.g. "cut out
coffee this week") -- food_name/food_target_frequency are set at generation
time, food_baseline_frequency is the established occurrences/week before the
experiment, and food_experiment_count is filled in at dismissal time as a
plain count of matching FoodEntry rows during the experiment week (no t-test,
unlike the workout outcome -- adherence isn't a significance question).

Revision ID: 00033
Revises: 00032
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa

revision = "00033"
down_revision = "00032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("food_name", sa.String, nullable=True))
        batch_op.add_column(sa.Column("food_baseline_frequency", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("food_target_frequency", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("food_experiment_count", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.drop_column("food_experiment_count")
        batch_op.drop_column("food_target_frequency")
        batch_op.drop_column("food_baseline_frequency")
        batch_op.drop_column("food_name")
