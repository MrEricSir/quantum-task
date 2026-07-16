"""Add project_name and project_status to engineering_items

Revision ID: 00018
Revises: 00017
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa

revision = '00018'
down_revision = '00017'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('engineering_items', sa.Column('project_name', sa.String(), nullable=True))
    op.add_column('engineering_items', sa.Column('project_status', sa.String(), nullable=True))


def downgrade():
    op.drop_column('engineering_items', 'project_status')
    op.drop_column('engineering_items', 'project_name')
