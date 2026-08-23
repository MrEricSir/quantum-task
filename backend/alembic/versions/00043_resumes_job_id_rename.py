"""rename bridge_jobs.fix_of_job_id to resumes_job_id

Found during a lead-engineer-style review of the branch workflow improvements plan: the
column already meant "resumes this job's worktree/branch" for BOTH a targeted fix (Phase A,
fix_comment_ids set) and a general resume-after-interruption (Phase 0, fix_comment_ids left
unset) -- keeping the fix-specific name caused a real bug during Phase 0's own development
(a hand-built test job dict assumed fix_of_job_id alone meant "this is a fix job"). Renamed
now, while only two endpoints (/fix and /resume) touch it, rather than let more code
accumulate around the misleading name.

Note for local development: this changes the bridge's own JSON wire format
(next/pending's response), so an already-installed local qtask-bridge binary needs to be
reinstalled (re-run the curl install command) after this migration ships -- it's not
auto-updated, and the old field name would otherwise silently fail to trigger the
fix/resume-resume code path (falls through to a fresh worktree attempt, which then errors
on a branch-already-exists collision).

Revision ID: 00043
Revises: 00042
Create Date: 2026-08-22
"""
from alembic import op

revision = "00043"
down_revision = "00042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain rename, nothing else -- verified empirically (against a real copy of the dev DB)
    # that alembic's SQLite batch-mode table recreation already carries the existing FK
    # constraint through an alter_column automatically (it just keeps its old, now slightly
    # stale name -- fk_bridge_jobs_fix_of_job_id -- which is harmless: SQLite doesn't use the
    # constraint name for enforcement). Explicitly drop_constraint-ing and re-creating it
    # alongside the rename in the same batch, tried first, actually LOST the FK entirely
    # under SQLite -- a real, reproduced footgun, not a hypothetical one. Don't reintroduce
    # that here.
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.alter_column("fix_of_job_id", new_column_name="resumes_job_id")


def downgrade() -> None:
    with op.batch_alter_table("bridge_jobs") as batch_op:
        batch_op.alter_column("resumes_job_id", new_column_name="fix_of_job_id")
