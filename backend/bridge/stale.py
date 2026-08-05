"""
Stale bridge job detection.

A job stuck at status "running" forever (crashed agent, laptop went to
sleep, network dropped) previously had no automatic recovery path. The
qtask-bridge agent now pings POST /api/bridge/jobs/{id}/heartbeat every
5 minutes while it runs (see bridge/scripts/agent_core.py's
_start_heartbeat) -- including during interactive sessions, which
otherwise post no output at all until the session ends. This module finds
jobs whose heartbeat has gone quiet and marks them "stalled".

Kept deliberately free of any Telegram/notification dependency -- see
bridge/router.py's check-stale endpoint for why: the DB transition must
happen unconditionally, regardless of whether Telegram is configured.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

import models

# 4x margin over one missed 5-minute heartbeat (agent_core.HEARTBEAT_INTERVAL)
# before concluding the agent process is actually gone, not just slow to ping.
STALE_THRESHOLD_MINUTES = 20


def check_stale_bridge_jobs(db: Session) -> list[models.BridgeJob]:
    """Transition any "running" job with no heartbeat/output in the last
    STALE_THRESHOLD_MINUTES to "stalled". Self-limiting: once transitioned,
    a job no longer matches status == "running", so calling this repeatedly
    never re-flags the same job twice."""
    # updated_at is a UTC-instant column (see models.py's timezone-convention
    # docstring) -- SQLite strips tzinfo on save, so it comes back naive.
    # Compare against a naive cutoff too, or the string comparison SQLite
    # falls back to for datetimes can silently misorder aware vs naive values.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=STALE_THRESHOLD_MINUTES)
    stale_jobs = (
        db.query(models.BridgeJob)
        .filter(
            models.BridgeJob.status == "running",
            models.BridgeJob.updated_at.isnot(None),
            models.BridgeJob.updated_at < cutoff,
        )
        .all()
    )
    for job in stale_jobs:
        job.status = "stalled"
    if stale_jobs:
        db.commit()
    return stale_jobs
