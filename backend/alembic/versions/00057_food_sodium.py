"""Add sodium_mg to food_entries

Backs correlating salt intake against Withings' hydration (water weight) reading -- sodium
intake is a well-documented driver of short-term water retention. LLM-estimated per item,
same pattern as the existing calories/quality estimation (capabilities/food.py).

Revision ID: 00057
Revises: 00056
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa

revision = "00057"
down_revision = "00056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("food_entries") as batch_op:
        batch_op.add_column(sa.Column("sodium_mg", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("food_entries") as batch_op:
        batch_op.drop_column("sodium_mg")
