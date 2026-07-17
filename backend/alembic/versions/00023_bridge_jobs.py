"""Add bridge_jobs table

Revision ID: 00023
Revises: 00022
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = '00023'
down_revision = '00022'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    if 'bridge_jobs' not in inspect(conn).get_table_names():
        op.create_table(
            'bridge_jobs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('card_id', sa.Integer(), sa.ForeignKey('cards.id', ondelete='CASCADE'), nullable=False),
            sa.Column('status', sa.String(), nullable=False, server_default='pending'),
            sa.Column('spec_snapshot', sa.Text(), nullable=True),
            sa.Column('prompt_snapshot', sa.Text(), nullable=True),
            sa.Column('result', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_bridge_jobs_card_id', 'bridge_jobs', ['card_id'])
        op.create_index('ix_bridge_jobs_status', 'bridge_jobs', ['status'])


def downgrade():
    op.drop_table('bridge_jobs')
