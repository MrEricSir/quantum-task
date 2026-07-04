"""Add today_since to cards

Revision ID: 00006
Revises: 00005
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa

revision = '00006'
down_revision = '00005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('cards', sa.Column('today_since', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('cards', 'today_since')
