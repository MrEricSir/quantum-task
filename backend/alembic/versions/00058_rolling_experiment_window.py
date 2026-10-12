"""Replace HealthExperiment.week (fixed ISO calendar week) with started_on/duration_days
(a rolling window)

Dismissing or skipping an experiment mid-week used to generate a replacement stamped with
the SAME ISO week string, so it only ran whatever days were left until the next Monday's
cron swept it up too -- and its outcome was scored against a denominator that always
assumed a full 7 days, unfairly penalizing a replacement that existed for far fewer. This
migration retires `week` in favor of a real per-row anchor (`started_on`, a local date)
and duration (`duration_days`, always 7 for now but a real column so a future variable-
duration experiment needs no second migration) -- so dismissing/skipping at any point
starts a genuine, undiluted window from that exact moment.

Also swaps the one-active-experiment partial unique index from keying on `week` (which
only ever worked because every active row happened to share the current week) to keying
on `status` directly -- the real invariant being enforced.

Revision ID: 00058
Revises: 00057
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from datetime import date, timedelta

revision = "00058"
down_revision = "00057"
branch_labels = None
depends_on = None


def _week_start(wk: str) -> date:
    # Frozen copy of routers/correlations.py's _week_start, for this one-time backfill
    # only -- same precedent _migrate_appsetting already sets for reading a frozen
    # historical format with its own self-contained logic rather than importing live
    # application code into a migration.
    y, w = int(wk[:4]), int(wk[6:])
    jan4 = date(y, 1, 4)
    return jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=w - 1)


def upgrade() -> None:
    conn = op.get_bind()
    op.add_column("health_experiments", sa.Column("started_on", sa.String(), nullable=True))
    op.add_column(
        "health_experiments",
        sa.Column("duration_days", sa.Integer(), nullable=False, server_default="7"),
    )

    rows = conn.execute(text("SELECT id, week FROM health_experiments")).fetchall()
    for row in rows:
        started_on = _week_start(row.week).isoformat()
        conn.execute(
            text("UPDATE health_experiments SET started_on = :s WHERE id = :id"),
            {"s": started_on, "id": row.id},
        )

    # Drop the old partial index BEFORE the batch rebuild below -- SQLite's batch-alter
    # table-rebuild tries to preserve every index it finds on the table, including this
    # one (still referencing `week`), and recreating it against the rebuilt table (which
    # no longer has `week`) fails with "no such column: week". Dropping it first means
    # there's nothing stale left for the rebuild to carry forward.
    op.execute("DROP INDEX IF EXISTS uq_health_experiments_active_week")

    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.alter_column("started_on", nullable=False)
        batch_op.drop_column("week")

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_health_experiments_one_active "
        "ON health_experiments(status) WHERE status = 'active'"
    )


def downgrade() -> None:
    # Same ordering requirement as upgrade(), mirrored: drop the current index before the
    # batch rebuild, add the old one back only once `week` actually exists again.
    op.execute("DROP INDEX IF EXISTS uq_health_experiments_one_active")

    with op.batch_alter_table("health_experiments") as batch_op:
        batch_op.add_column(sa.Column("week", sa.String(), nullable=True))
        batch_op.drop_column("started_on")
        batch_op.drop_column("duration_days")

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_health_experiments_active_week "
        "ON health_experiments(week) WHERE status = 'active'"
    )
