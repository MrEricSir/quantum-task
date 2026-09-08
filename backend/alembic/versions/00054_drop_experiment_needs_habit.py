"""Drop needs_habit from health_experiments

needs_habit had drifted from what actually gates habit creation in
_generate_experiment (routers/correlations.py) -- action is the real gate,
unconditional on needs_habit's value. Real rows existed with needs_habit=0
but a populated habit_id, confirming the field no longer reflected the
code's actual behavior. Its one frontend usage (an "A tracking habit has
been created for you" note) was also confirmed practically unreachable,
since showProgress is true whenever habit_id is set.

Revision ID: 00054
Revises: 00053
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = "00054"
down_revision = "00053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.drop_column("needs_habit")


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("needs_habit", sa.Boolean(), nullable=True))
