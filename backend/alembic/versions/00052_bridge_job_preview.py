"""Add preview_status and preview_url to bridge_jobs

Backs the bridge-managed preview server (config.toml's auto_preview): after a successful
job, agent_core.py's _start_preview launches the worktree's Procfile/run_cmd detached and
reports progress through these two columns, independent of the job's own status -- a job can
be "done" while its preview process keeps running. preview_status moves through
starting -> running (with preview_url set) or failed, and later stopped once
--stop-preview/--cleanup tears the process down. See QTASK_WORKFLOW_REVIEW.md and
PRODUCT_NOTES.md's "Bridge-managed preview server" entries.

Revision ID: 00052
Revises: 00051
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa

revision = "00052"
down_revision = "00051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.add_column(sa.Column("preview_status", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("preview_url", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.drop_column("preview_url")
        batch_op.drop_column("preview_status")
