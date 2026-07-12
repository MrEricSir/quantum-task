"""Add card_threads table for persistent multi-turn assistant conversations

Revision ID: 00014
Revises: 00013
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa

revision = '00014'
down_revision = '00013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'card_threads',
        sa.Column('id',         sa.Integer,  primary_key=True),
        sa.Column('card_id',    sa.Integer,  sa.ForeignKey('cards.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('context',    sa.Text,     nullable=True),
        sa.Column('messages',   sa.Text,     nullable=False, server_default='[]'),
        sa.Column('output',     sa.Text,     nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=True),
    )


def downgrade():
    op.drop_table('card_threads')
