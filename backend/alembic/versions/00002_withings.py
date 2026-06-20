"""add withings measurements table and habit goal columns

Revision ID: 00002
Revises: 00001
Create Date: 2026-06-19 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "00002"
down_revision = "00001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "withings_measurements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("date", sa.String, nullable=False),
        sa.Column("metric", sa.String, nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("synced_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint("date", "metric", name="uq_withings_date_metric"),
    )

    with op.batch_alter_table("habits") as batch_op:
        batch_op.add_column(sa.Column("withings_metric", sa.String, nullable=True))
        batch_op.add_column(sa.Column("withings_goal", sa.Float, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("habits") as batch_op:
        batch_op.drop_column("withings_goal")
        batch_op.drop_column("withings_metric")

    op.drop_table("withings_measurements")
