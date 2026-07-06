"""Add WithingsCredentials table; migrate AppSetting blob to typed columns.

Revision ID: 00012
Revises: 00011
Create Date: 2026-07-05
"""
import json
from datetime import datetime

from alembic import op
from sqlalchemy import Column, DateTime, Integer, String, text

revision = '00012'
down_revision = '00011'
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    result = op.get_bind().execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    )
    return result.fetchone() is not None


def upgrade():
    if not _table_exists("withings_credentials"):
        op.create_table(
            "withings_credentials",
            Column("id",              Integer, primary_key=True, autoincrement=True),
            Column("access_token",    String,  nullable=False),
            Column("token_type",      String,  nullable=False),
            Column("refresh_token",   String,  nullable=False),
            Column("userid",          Integer, nullable=False),
            Column("client_id",       String,  nullable=False),
            Column("consumer_secret", String,  nullable=False),
            Column("expires_in",      Integer, nullable=False),
            Column("last_synced",     DateTime, nullable=True),
        )

    conn = op.get_bind()

    # Migrate existing JSON blob from app_settings, if present
    old_creds = conn.execute(
        text("SELECT value FROM app_settings WHERE key='withings_credentials'")
    ).fetchone()
    if old_creds:
        try:
            data = json.loads(old_creds[0])
        except Exception:
            data = None

        if data:
            last_synced = None
            last_row = conn.execute(
                text("SELECT value FROM app_settings WHERE key='withings_last_synced'")
            ).fetchone()
            if last_row:
                try:
                    last_synced = datetime.fromisoformat(last_row[0])
                except Exception:
                    pass

            existing = conn.execute(
                text("SELECT COUNT(*) FROM withings_credentials")
            ).fetchone()
            if existing[0] == 0:
                conn.execute(
                    text("""
                        INSERT INTO withings_credentials
                            (access_token, token_type, refresh_token, userid,
                             client_id, consumer_secret, expires_in, last_synced)
                        VALUES
                            (:access_token, :token_type, :refresh_token, :userid,
                             :client_id, :consumer_secret, :expires_in, :last_synced)
                    """),
                    {
                        "access_token":    data.get("access_token", ""),
                        "token_type":      data.get("token_type", "Bearer"),
                        "refresh_token":   data.get("refresh_token", ""),
                        "userid":          int(data.get("userid", 0)),
                        "client_id":       data.get("client_id", ""),
                        "consumer_secret": data.get("consumer_secret", ""),
                        "expires_in":      int(data.get("expires_in", 10800)),
                        "last_synced":     last_synced,
                    },
                )
                print("[migration 00012] migrated Withings credentials to typed table")

        conn.execute(text("DELETE FROM app_settings WHERE key='withings_credentials'"))
        conn.execute(text("DELETE FROM app_settings WHERE key='withings_last_synced'"))
        conn.commit()


def downgrade():
    pass  # Data migration cannot be reversed meaningfully
