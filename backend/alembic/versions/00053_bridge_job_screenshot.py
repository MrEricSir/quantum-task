"""Add screenshot_data to bridge_jobs

Backs visual verification (config.toml's visual_verify, requires auto_preview): once a
preview is confirmed running, agent_core.py's _capture_preview_screenshot shells out to
`npx playwright screenshot`, base64-encodes the PNG, and POSTs it to
/api/bridge/jobs/{id}/screenshot, which stores it here and (best-effort) forwards it to
Telegram. Stored inline as base64 text rather than object storage -- see the column's own
comment in models.py for the tradeoff reasoning. See PRODUCT_NOTES.md's "Visual verification"
entry.

Revision ID: 00053
Revises: 00052
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa

revision = "00053"
down_revision = "00052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.add_column(sa.Column("screenshot_data", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.drop_column("screenshot_data")
