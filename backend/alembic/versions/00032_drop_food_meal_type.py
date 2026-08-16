"""Drop meal_type from food_entries (meal-type bucketing removed)

meal_type was LLM-guessed and unreliable -- the Telegram logging path never
even gave the LLM the current hour to work with, and the webapp path's LLM
call sometimes ignored its own explicit hour-based rule. It was also only
ever used for a display grouping on the Health page, not for any analysis,
so it wasn't worth trying to make the classification more reliable.

Revision ID: 00032
Revises: 00031
Create Date: 2026-08-15
"""
from alembic import op
from sqlalchemy import inspect

revision = '00032'
down_revision = '00031'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = [c['name'] for c in inspect(bind).get_columns('food_entries')]
    if 'meal_type' in cols:
        with op.batch_alter_table('food_entries') as batch_op:
            batch_op.drop_column('meal_type')


def downgrade():
    import sqlalchemy as sa
    with op.batch_alter_table('food_entries') as batch_op:
        batch_op.add_column(sa.Column('meal_type', sa.String(), nullable=True))
