"""Add checkpoint_matched_paths to bridge_jobs

Backs the checkpoint gate for unattended jobs: if a job's diff touches a configured
checkpoint pattern (app_setting_keys.CHECKPOINT_PATTERNS), it lands in a new
"needs_confirmation" status (no CHECK constraint on the existing status column, so no
migration needed for that part) with the matched paths recorded here for display. See
QTASK_WORKFLOW_REVIEW.md's "watch/unattended middle ground" entry and
bridge/scripts/agent_core.py's _match_checkpoint_patterns.

Revision ID: 00050
Revises: 00049
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa

revision = "00050"
down_revision = "00049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.add_column(sa.Column("checkpoint_matched_paths", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.drop_column("checkpoint_matched_paths")
