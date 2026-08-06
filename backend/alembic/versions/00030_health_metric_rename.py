"""rename withings_metric/withings_goal to health_metric/health_goal

Revision ID: 00030
Revises: 00029
Create Date: 2026-08-06 00:00:00.000000

Part of the pluggable-integrations naming cleanup (ARCHITECTURE_FUTURE.md Part 2,
Step 1) -- these columns link a habit or health experiment to a metric threshold,
a generic concept that shouldn't be named after the one provider (Withings) that
happens to supply the measurement today.
"""
from alembic import op

revision = "00030"
down_revision = "00029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("habits") as batch_op:
        batch_op.alter_column("withings_metric", new_column_name="health_metric")
        batch_op.alter_column("withings_goal", new_column_name="health_goal")

    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.alter_column("withings_metric", new_column_name="health_metric")
        batch_op.alter_column("withings_goal", new_column_name="health_goal")


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.alter_column("health_goal", new_column_name="withings_goal")
        batch_op.alter_column("health_metric", new_column_name="withings_metric")

    with op.batch_alter_table("habits") as batch_op:
        batch_op.alter_column("health_goal", new_column_name="withings_goal")
        batch_op.alter_column("health_metric", new_column_name="withings_metric")
