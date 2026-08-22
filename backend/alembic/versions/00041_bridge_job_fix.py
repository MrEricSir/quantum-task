"""add fix_of_job_id/fix_comment_ids to bridge_jobs

Part of the CodeRabbit feedback integration's apply-feedback mechanism
(CLAUDE_CODE_INTEGRATION.md, Phase A): a "fix" BridgeJob resumes an existing worktree
(fix_of_job_id points at the original job whose branch/worktree it reuses) instead of
creating a fresh one, scoped to specific review comments (fix_comment_ids, a JSON list of
EngineeringItemComment ids).

Revision ID: 00041
Revises: 00040
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "00041"
down_revision = "00040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.add_column(sa.Column("fix_of_job_id", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("fix_comment_ids", sa.Text, nullable=True))
        batch_op.create_foreign_key(
            "fk_bridge_jobs_fix_of_job_id", "bridge_jobs", ["fix_of_job_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.drop_constraint("fk_bridge_jobs_fix_of_job_id", type_="foreignkey")
        batch_op.drop_column("fix_comment_ids")
        batch_op.drop_column("fix_of_job_id")
