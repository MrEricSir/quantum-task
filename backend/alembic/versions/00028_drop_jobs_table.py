"""Drop jobs table (Workshop feature removed)

The jobs table backed the Workshop page, which has been removed. It was
never created via a migration (SQLAlchemy's create_all made it at startup),
so this drop is guarded with an existence check.

Revision ID: 00028
Revises: 00027
Create Date: 2026-07-29
"""
from alembic import op
from sqlalchemy import inspect

revision = '00028'
down_revision = '00027'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if 'jobs' in inspect(bind).get_table_names():
        op.drop_table('jobs')


def downgrade():
    pass  # jobs table is gone for good; Workshop feature was removed
