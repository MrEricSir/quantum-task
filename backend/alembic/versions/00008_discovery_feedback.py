"""add discovery_feedback table

Revision ID: 00008
Revises: 00007
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa

revision = '00008'
down_revision = '00007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'discovery_feedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_uid', sa.String(), nullable=False),
        sa.Column('event_title', sa.String(), nullable=False),
        sa.Column('event_description', sa.String(), nullable=True),
        sa.Column('interested', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_uid'),
    )
    op.create_index(op.f('ix_discovery_feedback_id'), 'discovery_feedback', ['id'], unique=False)
    op.create_index(op.f('ix_discovery_feedback_event_uid'), 'discovery_feedback', ['event_uid'], unique=True)


def downgrade():
    op.drop_index(op.f('ix_discovery_feedback_event_uid'), table_name='discovery_feedback')
    op.drop_index(op.f('ix_discovery_feedback_id'), table_name='discovery_feedback')
    op.drop_table('discovery_feedback')
