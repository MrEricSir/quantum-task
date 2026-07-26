"""Add workout_entries table

Revision ID: 00027
Revises: 00026
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa

revision = '00027'
down_revision = '00026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'workout_entries',
        sa.Column('id',         sa.Integer(),  nullable=False),
        sa.Column('raw_input',  sa.String(),   nullable=False),
        sa.Column('type',       sa.String(),   nullable=False),
        sa.Column('value',      sa.Float(),    nullable=True),
        sa.Column('unit',       sa.String(),   nullable=True),
        sa.Column('notes',      sa.String(),   nullable=True),
        sa.Column('logged_at',  sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_workout_entries_id', 'workout_entries', ['id'])


def downgrade():
    op.drop_index('ix_workout_entries_id', table_name='workout_entries')
    op.drop_table('workout_entries')
