"""Add worktree_path to bridge_jobs

Revision ID: 00029
Revises: 00028
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

revision = '00029'
down_revision = '00028'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bridge_jobs', sa.Column('worktree_path', sa.String(), nullable=True))


def downgrade():
    op.drop_column('bridge_jobs', 'worktree_path')
