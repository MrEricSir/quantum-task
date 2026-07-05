"""formalize legacy hand-rolled column additions

These columns were previously added by hand-rolled ALTER TABLE statements in
_run_startup_migrations(). Moving them here makes schema history traceable and
removes the silent-swallowing try/except blocks from startup code.

The upgrade is idempotent: PRAGMA table_info is checked before each ALTER so
running against a database that already has these columns is safe.

Revision ID: 00009
Revises: 00008
Create Date: 2026-07-04
"""
from alembic import op
from sqlalchemy import text

revision = '00009'
down_revision = '00008'
branch_labels = None
depends_on = None


def _existing_columns(conn, table):
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _add_if_missing(conn, table, col, defn):
    if col not in _existing_columns(conn, table):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {defn}"))
        print(f"[00009] added {table}.{col}")


def upgrade():
    conn = op.get_bind()

    # cards — columns added incrementally before Alembic was in place
    for col, defn in [
        ("completed_at",    "DATETIME"),
        ("raw_input",       "TEXT"),
        ("recurrence_rule", "TEXT"),
        ("external_id",     "TEXT"),
        ("body",            "TEXT"),
        ("updated_at",      "DATETIME"),
        ("archived",        "BOOLEAN DEFAULT 0"),
        ("archived_at",     "DATETIME"),
    ]:
        _add_if_missing(conn, "cards", col, defn)

    # habits
    for col, defn in [
        ("archived",    "BOOLEAN DEFAULT 0"),
        ("archived_at", "DATETIME"),
    ]:
        _add_if_missing(conn, "habits", col, defn)

    # notes — may not exist on databases that pre-date the notes feature
    notes_exists = conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notes'"
    )).fetchone()
    if notes_exists:
        for col, defn in [
            ("archived",    "BOOLEAN DEFAULT 0"),
            ("archived_at", "DATETIME"),
        ]:
            _add_if_missing(conn, "notes", col, defn)

    conn.commit()


def downgrade():
    # SQLite does not support DROP COLUMN; these additions are backward-compatible
    pass
