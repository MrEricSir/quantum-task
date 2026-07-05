"""Add iCal caching columns to event_discovery_feeds

Adds last_fetched (DateTime) and cached_events (Text) so the discovery
endpoint can skip redundant iCal fetches within a 3-hour window.

Revision ID: 00010
Revises: 00009
Create Date: 2026-07-05
"""
from alembic import op
from sqlalchemy import text

revision = '00010'
down_revision = '00009'
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    result = op.get_bind().execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result}


def upgrade():
    cols = _existing_columns("event_discovery_feeds")
    with op.batch_alter_table("event_discovery_feeds") as batch:
        if "last_fetched" not in cols:
            import sqlalchemy as sa
            batch.add_column(sa.Column("last_fetched", sa.DateTime, nullable=True))
        if "cached_events" not in cols:
            import sqlalchemy as sa
            batch.add_column(sa.Column("cached_events", sa.Text, nullable=True))


def downgrade():
    pass  # SQLite doesn't support DROP COLUMN
