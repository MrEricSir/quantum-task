"""add food_baseline_weeks_n to health_experiments

Tracks how many weeks fed a food-elimination experiment's weight/fat
baseline when it was computed from weeks the food was actually present
(rather than the generic all-other-weeks average every experiment type
uses by default) -- lets the outcome card disclose sample size the same
way workout_baseline_n/workout_experiment_n already do.

Revision ID: 00034
Revises: 00033
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa

revision = "00034"
down_revision = "00033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("food_baseline_weeks_n", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.drop_column("food_baseline_weeks_n")
