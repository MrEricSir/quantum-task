"""Drop legacy notes and note_tags tables

All notes were migrated to cards (section='none') via the notes_migrated_v1
AppSetting flag during startup. These tables are now dead code.

Revision ID: 00011
Revises: 00010
Create Date: 2026-07-05
"""
from alembic import op
from sqlalchemy import text

revision = '00011'
down_revision = '00010'
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    result = op.get_bind().execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    )
    return result.fetchone() is not None


def upgrade():
    if _table_exists("note_tags"):
        op.drop_table("note_tags")
    if _table_exists("notes"):
        op.drop_table("notes")


def downgrade():
    pass  # Notes data is gone; no meaningful rollback possible
