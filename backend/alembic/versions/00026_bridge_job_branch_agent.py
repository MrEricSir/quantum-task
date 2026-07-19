"""Add branch_name and agent_name to bridge_jobs

Revision ID: 00026
Revises: 00025
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa

revision = '00026'
down_revision = '00025'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bridge_jobs', sa.Column('branch_name', sa.String(), nullable=True))
    op.add_column('bridge_jobs', sa.Column('agent_name',  sa.String(), nullable=True))


def downgrade():
    op.drop_column('bridge_jobs', 'branch_name')
    op.drop_column('bridge_jobs', 'agent_name')
