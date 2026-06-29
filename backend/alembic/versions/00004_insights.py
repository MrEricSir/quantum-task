"""add snoozed_until and waiting_reason to cards

Revision ID: 00004
Revises: 00003
Create Date: 2026-06-28 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "00004"
down_revision = "00003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cards") as batch_op:
        batch_op.add_column(sa.Column("snoozed_until", sa.String, nullable=True))
        batch_op.add_column(sa.Column("waiting_reason", sa.String, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cards") as batch_op:
        batch_op.drop_column("snoozed_until")
        batch_op.drop_column("waiting_reason")
