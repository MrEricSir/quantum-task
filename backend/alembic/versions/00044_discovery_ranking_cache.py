"""Add discovery_ranking_cache table

Persists LLM event-ranking results across Cloud Run cold starts -- the in-memory
_ranking_cache dict in discovery.py is wiped every time a new instance spins up
(min-instances=0), so most opens were a cache miss even when nothing about the
user's interests or the event set had actually changed. See
DISCOVERY_IMPROVEMENTS.md Phase 5.

Revision ID: 00044
Revises: 00043
Create Date: 2026-08-24
"""
from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa

revision = "00044"
down_revision = "00043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "discovery_ranking_cache" not in inspect(bind).get_table_names():
        op.create_table(
            "discovery_ranking_cache",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("rkey", sa.String(), nullable=False),
            sa.Column("results_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_discovery_ranking_cache_id", "discovery_ranking_cache", ["id"])
        op.create_index(
            "ix_discovery_ranking_cache_rkey", "discovery_ranking_cache", ["rkey"], unique=True
        )


def downgrade() -> None:
    op.drop_index("ix_discovery_ranking_cache_rkey", table_name="discovery_ranking_cache")
    op.drop_index("ix_discovery_ranking_cache_id", table_name="discovery_ranking_cache")
    op.drop_table("discovery_ranking_cache")
