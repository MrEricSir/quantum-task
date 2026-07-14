"""Add mood_logs table

Revision ID: 00016
Revises: 00015
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa

revision = '00016'
down_revision = '00015'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    bind = op.get_bind()
    if 'mood_logs' not in inspect(bind).get_table_names():
        op.create_table(
            'mood_logs',
            sa.Column('id',         sa.Integer,  primary_key=True),
            sa.Column('date',       sa.String,   nullable=False, unique=True),
            sa.Column('energy',     sa.Integer,  nullable=False),
            sa.Column('note',       sa.String,   nullable=True),
            sa.Column('created_at', sa.DateTime, nullable=True),
            sa.Column('updated_at', sa.DateTime, nullable=True),
        )


def downgrade():
    op.drop_table('mood_logs')
