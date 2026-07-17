"""Add spec column to cards

Revision ID: 00022
Revises: 00021
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = '00022'
down_revision = '00021'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    existing = {c["name"] for c in inspect(conn).get_columns("cards")}
    if "spec" not in existing:
        op.add_column("cards", sa.Column("spec", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("cards", "spec")
