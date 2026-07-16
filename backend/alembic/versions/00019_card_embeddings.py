"""Add card_embeddings table

Revision ID: 00019
Revises: 00018
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = '00019'
down_revision = '00018'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'card_embeddings',
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('cards.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('embedding', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('card_embeddings')
