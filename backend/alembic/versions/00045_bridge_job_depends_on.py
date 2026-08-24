"""add depends_on_job_id to bridge_jobs

Part of cross-repo bridge jobs (BRIDGE_CROSS_REPO_JOBS.md, Phase 1): a companion job
targeting a different repo than the card's own GitHub link can be queued to run only
after another job finishes. depends_on_job_id points at that upstream job; a job with
it set is created with status="blocked" instead of "pending" and is unblocked by a
scheduler tick once the referenced job reaches "done" (Phase 2). Distinct from
resumes_job_id, which is same-repo continuation of one job, not cross-repo sequencing
between two independent jobs.

Revision ID: 00045
Revises: 00044
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "00045"
down_revision = "00044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.add_column(sa.Column("depends_on_job_id", sa.Integer, nullable=True))
        batch_op.create_foreign_key(
            "fk_bridge_jobs_depends_on_job_id", "bridge_jobs", ["depends_on_job_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.drop_constraint("fk_bridge_jobs_depends_on_job_id", type_="foreignkey")
        batch_op.drop_column("depends_on_job_id")
