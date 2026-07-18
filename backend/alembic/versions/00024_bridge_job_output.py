"""Add output column to bridge_jobs

Revision ID: 00024
Revises: 00023
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa

revision = '00024'
down_revision = '00023'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bridge_jobs', sa.Column('output', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('bridge_jobs', 'output')
