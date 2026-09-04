"""Add self_review_flagged to bridge_jobs

Backs the automatic self-review pass (config.toml's self_review, mirroring test_cmd/
verify_acceptance's existing opt-in plumbing -- see agent_core.py's _run_self_review /
_parse_review_verdict): if the verdict comes back ISSUES_FOUND or unparseable, the job lands
in "needs_confirmation" -- the same status the checkpoint gate already uses, OR'd together in
_report_job_finished. Kept as its own column (not folded into checkpoint_matched_paths) so the
frontend can tell the two triggers apart. See PRODUCT_NOTES.md's "Automatic/server-triggered
review" and QTASK_WORKFLOW_REVIEW.md's "watch/unattended middle ground" entries.

Revision ID: 00051
Revises: 00050
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa

revision = "00051"
down_revision = "00050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.add_column(sa.Column("self_review_flagged", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.drop_column("self_review_flagged")
