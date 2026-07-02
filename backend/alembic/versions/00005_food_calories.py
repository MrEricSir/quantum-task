"""add calories to food_entries

Revision ID: 00005
Revises: 00004
Create Date: 2026-07-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "00005"
down_revision = "00004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("food_entries") as batch_op:
        batch_op.add_column(sa.Column("calories", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("food_entries") as batch_op:
        batch_op.drop_column("calories")
