"""add confounds to health_experiments, drop per-type calorie confound columns

Generalizes the experiment confound check: previously only food/workout experiments got
a single-variable calorie confound check (food_baseline_avg_calories/
workout_baseline_avg_calories and their _experiment_ counterparts). Habit experiments
(e.g. "sleep 8 hours") got no confound check at all. Replaced with one generic JSON
column (confounds) computed for every experiment type, checking both avg_calories and
avg_steps. See correlations.py's _confound_summary, and PRODUCT_NOTES.md's
"Multivariate/confound-adjusted health experiments" for background on why a full
multivariate model stays out of scope.

Revision ID: 00047
Revises: 00046
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa

revision = "00047"
down_revision = "00046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("confounds", sa.Text, nullable=True))
        batch_op.drop_column("food_baseline_avg_calories")
        batch_op.drop_column("food_experiment_avg_calories")
        batch_op.drop_column("workout_baseline_avg_calories")
        batch_op.drop_column("workout_experiment_avg_calories")


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("food_baseline_avg_calories", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("food_experiment_avg_calories", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("workout_baseline_avg_calories", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("workout_experiment_avg_calories", sa.Float, nullable=True))
        batch_op.drop_column("confounds")
