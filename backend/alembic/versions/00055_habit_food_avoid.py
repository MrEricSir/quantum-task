"""Add food_avoid_name/food_avoid_target to habits

Backs auto-tracking for food-elimination experiment habits (see
correlations.py's check_food_avoidance_habits): the linked Habit needs its
own copy of the target food/frequency so it can be auto-checked off from the
food log, the same way a Withings-goal habit is auto-checked via
health_metric/health_goal, instead of requiring a manual daily tap whose
semantics ("check it if I ate it, or if I didn't?") were never well-defined.

Revision ID: 00055
Revises: 00054
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = "00055"
down_revision = "00054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("habits") as batch_op:
        batch_op.add_column(sa.Column("food_avoid_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("food_avoid_target", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("habits") as batch_op:
        batch_op.drop_column("food_avoid_target")
        batch_op.drop_column("food_avoid_name")
