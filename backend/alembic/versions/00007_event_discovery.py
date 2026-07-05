"""create event_discovery_feeds table

Revision ID: 00007
Revises: 00006
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa

revision = '00007'
down_revision = '00006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'event_discovery_feeds',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False, server_default=''),
        sa.Column('ical_url', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_event_discovery_feeds_id'),
        'event_discovery_feeds', ['id'], unique=False,
    )


def downgrade():
    op.drop_index(op.f('ix_event_discovery_feeds_id'), table_name='event_discovery_feeds')
    op.drop_table('event_discovery_feeds')
