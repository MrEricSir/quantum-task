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


def _mark_stalled_if_still_running(db: Session, job_id: int) -> bool:
    """Atomically flips one job running -> stalled, conditioned on it still being
    running at the moment this UPDATE actually runs -- not just when the stale-sweep's
    candidate SELECT found it.

    A real POST /complete or /needs-confirmation can land in the gap between that
    SELECT and this write; without the status='running' guard here, this would be a
    blind write that silently clobbers the real outcome back to "stalled". The
    status='running' condition makes the write a no-op (rowcount 0) when that
    happens, so check_stale_bridge_jobs below can simply skip the job instead of
    overwriting a real, newer status. Same pattern as
    bridge.jobs._claim_job_if_still_pending (that one guards pending->running, this
    one guards running->stalled)."""
    updated = (
        db.query(models.BridgeJob)
        .filter_by(id=job_id, status="running")
        .update({"status": "stalled"})
    )
    db.commit()
    return updated > 0


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
    candidates = (
        db.query(models.BridgeJob)
        .filter(
            models.BridgeJob.status == "running",
            models.BridgeJob.updated_at.isnot(None),
            models.BridgeJob.updated_at < cutoff,
        )
        .all()
    )
    stale_jobs = []
    for candidate in candidates:
        if _mark_stalled_if_still_running(db, candidate.id):
            db.refresh(candidate)
            stale_jobs.append(candidate)
    return stale_jobs
