"""Add target_repo to bridge_jobs

Revision ID: 00025
Revises: 00024
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa

revision = '00025'
down_revision = '00024'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bridge_jobs', sa.Column('target_repo', sa.String(), nullable=True))


def downgrade():
    op.drop_column('bridge_jobs', 'target_repo')
