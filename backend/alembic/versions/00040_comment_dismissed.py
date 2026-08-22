"""add dismissed/dismissed_at to engineering_item_comments

Part of "CodeRabbit feedback integration"'s curation UI (CLAUDE_CODE_INTEGRATION.md):
a per-comment "I've seen/handled this" flag so a declined suggestion doesn't keep
resurfacing on every poll. Purely local state -- github_sync.py's sync never touches these
columns on an existing row, so re-syncing an already-dismissed comment never un-dismisses it.

Revision ID: 00040
Revises: 00039
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "00040"
down_revision = "00039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("engineering_item_comments") as batch_op:
        batch_op.add_column(sa.Column("dismissed", sa.Boolean, nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("dismissed_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("engineering_item_comments") as batch_op:
        batch_op.drop_column("dismissed_at")
        batch_op.drop_column("dismissed")
