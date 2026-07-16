"""Add engineering_item_embeddings table

Revision ID: 00020
Revises: 00019
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = '00020'
down_revision = '00019'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    if 'engineering_item_embeddings' not in inspect(conn).get_table_names():
        op.create_table(
            'engineering_item_embeddings',
            sa.Column('item_id', sa.Integer(), sa.ForeignKey('engineering_items.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('embedding', sa.Text(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )


def downgrade():
    op.drop_table('engineering_item_embeddings')
