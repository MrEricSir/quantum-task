"""add diff_summary to bridge_jobs

Part of cross-repo bridge jobs (BRIDGE_CROSS_REPO_JOBS.md, Phase 4): the bridge CLI captures
`git diff --stat` against the primary branch client-side at job-completion time -- no GitHub
PR or push required -- and reports it alongside the existing free-text result note. Kept in
a separate column so a single-repo job's existing result display doesn't get noisier; only
pulled into a cross-repo companion job's prompt by bridge/unblock.py.

Revision ID: 00046
Revises: 00045
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "00046"
down_revision = "00045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.add_column(sa.Column("diff_summary", sa.Text, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.drop_column("diff_summary")
