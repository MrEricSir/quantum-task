"""add food calorie-confound fields to health_experiments

One-variable confound check for food-elimination experiments: average daily
calories across the weeks used for the food-specific baseline vs. the
experiment week, so a user can see whether overall intake also changed
before crediting the specific food. Cheaper than a full multivariate model,
which the app's typical ~12-13 weeks of paired data can't reliably support
(see PRODUCT_NOTES.md's Health & Habits section for the deferred plan).

Revision ID: 00035
Revises: 00034
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa

revision = "00035"
down_revision = "00034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("food_baseline_avg_calories", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("food_experiment_avg_calories", sa.Float, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.drop_column("food_experiment_avg_calories")
        batch_op.drop_column("food_baseline_avg_calories")
