"""Add live_preview_url_used to bridge_jobs

Backs the companion-job live-preview feature: a cross-repo companion job
(depends_on_job_id set) now has the upstream job's preview checked fresh at claim time
(GET /api/bridge/jobs/next/pending), not at unblock time -- the upstream's auto_preview
doesn't even attempt to start until after its own /complete call returns, the same request
that triggers the unblock, so checking any earlier would almost always find nothing yet.
This column records the upstream's preview_url when it was actually "running" at that exact
claim moment, so the Code tab can show it as its own UI signal, same pattern
checkpoint_matched_paths/self_review_flagged already use. See the column's own comment in
models.py and PRODUCT_NOTES.md's qtask-bridge entry.

Revision ID: 00059
Revises: 00058
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa

revision = "00059"
down_revision = "00058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.add_column(sa.Column("live_preview_url_used", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.drop_column("live_preview_url_used")
