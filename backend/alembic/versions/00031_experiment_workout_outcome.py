"""add workout-linked fields to health_experiments

Revision ID: 00031
Revises: 00030
Create Date: 2026-08-08 00:00:00.000000

Lets a weekly health experiment reference an established workout routine
(e.g. "row 2mi/day instead of 1mi") and record a genuine before/after
comparison -- workout_type/target/unit are set at generation time,
workout_baseline_*/workout_experiment_*/workout_p are filled in at
dismissal time by the same ttest_ind comparison _compute_segments already
uses for the weight/fat correlation segments.
"""
from alembic import op
import sqlalchemy as sa

revision = "00031"
down_revision = "00030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("workout_type", sa.String, nullable=True))
        batch_op.add_column(sa.Column("workout_target_value", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("workout_unit", sa.String, nullable=True))
        batch_op.add_column(sa.Column("workout_baseline_avg", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("workout_experiment_avg", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("workout_baseline_n", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("workout_experiment_n", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("workout_p", sa.Float, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.drop_column("workout_p")
        batch_op.drop_column("workout_experiment_n")
        batch_op.drop_column("workout_baseline_n")
        batch_op.drop_column("workout_experiment_avg")
        batch_op.drop_column("workout_baseline_avg")
        batch_op.drop_column("workout_unit")
        batch_op.drop_column("workout_target_value")
        batch_op.drop_column("workout_type")
