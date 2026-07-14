"""Add is_project to tags

Revision ID: 00017
Revises: 00016
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa

revision = '00017'
down_revision = '00016'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect, text
    bind = op.get_bind()
    cols = [c['name'] for c in inspect(bind).get_columns('tags')]
    if 'is_project' not in cols:
        op.add_column('tags', sa.Column('is_project', sa.Boolean(), nullable=False, server_default='0'))
        # Migrate existing tags whose name follows the "Project: " convention
        bind.execute(text("UPDATE tags SET is_project = 1 WHERE name LIKE 'Project: %'"))


def downgrade():
    op.drop_column('tags', 'is_project')
