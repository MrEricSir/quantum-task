"""add routine_type diagnostic column to health_experiments

Persists the raw "routine_type" the LLM returned during experiment generation,
independent of which field-group (health_metric vs workout_type vs food_name)
ends up actually populated on the row. Added after finding a real experiment
whose action text was clearly workout-shaped but whose workout_type ended up
null and whose health_metric/health_goal (for an unrelated idea) survived
instead -- with no raw LLM response persisted anywhere, this kind of drift was
previously undiagnosable after the fact. Purely diagnostic; no behavior change.

Revision ID: 00037
Revises: 00036
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa

revision = "00037"
down_revision = "00036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("routine_type", sa.String, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.drop_column("routine_type")
