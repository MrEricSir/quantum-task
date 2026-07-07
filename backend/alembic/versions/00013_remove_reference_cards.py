"""Migrate section='none' reference cards to section='later' (Stash)

Reference cards (section='none') are invisible in the UI — they were a
hidden "capture" concept that never surfaced well.  Replacing them with
Stash cards (section='later') makes all captured items visible and actionable.

Revision ID: 00013
Revises: 00012
Create Date: 2026-07-06
"""
from alembic import op
from sqlalchemy import text

revision = '00013'
down_revision = '00012'
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().execute(
        text("UPDATE cards SET section = 'later' WHERE section = 'none'")
    )


def downgrade():
    pass  # No meaningful rollback — original section='none' intent is removed
