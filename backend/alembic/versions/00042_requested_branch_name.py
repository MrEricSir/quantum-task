"""add requested_branch_name to bridge_jobs

Part of the branch workflow improvements plan (CLAUDE_CODE_INTEGRATION.md, Phase 1): lets a
user override the auto-generated qtask/<card_id>-<slug> branch name at queue time from the
Code tab. Kept separate from the existing branch_name column, which stays "what the bridge
actually reports back via /start" (fact) rather than colliding with this (intent).

Revision ID: 00042
Revises: 00041
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa

revision = "00042"
down_revision = "00041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.add_column(sa.Column("requested_branch_name", sa.String, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.drop_column("requested_branch_name")
