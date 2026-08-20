"""add source column to withings_measurements

Distinguishes device-synced readings from user-typed manual entries (health manual entry
feature) so the frontend's manual-entry list can show only rows the user can meaningfully
edit/delete, and so Telegram/insights code can tell whether a reading came from a connected
device or was hand-typed. Existing rows default to "withings" (the only source that existed
before this column). A row's source is overwritten on every upsert, so if a later Withings
sync ever re-populates a date/metric a user entered manually, the row becomes attributed to
"withings" again -- device data is treated as authoritative if it shows up.

Revision ID: 00038
Revises: 00037
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "00038"
down_revision = "00037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("withings_measurements") as batch_op:
        batch_op.add_column(sa.Column("source", sa.String, nullable=False, server_default="withings"))


def downgrade() -> None:
    with op.batch_alter_table("withings_measurements") as batch_op:
        batch_op.drop_column("source")
