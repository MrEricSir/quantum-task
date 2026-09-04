"""
Cross-repo companion job unblocking.

A companion job with depends_on_job_id set is created "blocked" instead of "pending" (see
bridge/jobs.py's _queue_job_for_card and BRIDGE_CROSS_REPO_JOBS.md Phase 1). This module
transitions it to "pending" (claimable) once its upstream job finishes ("done" or
"needs_confirmation" -- see _UPSTREAM_FINISHED_STATUSES below), appending the upstream job's
completion note to the downstream job's prompt so the companion job reflects what was actually
built, not just the original plan.

Kept deliberately free of any Telegram/notification dependency, same reasoning as
bridge/stale.py: the DB transition must happen unconditionally, regardless of whether
Telegram is configured.
"""
from sqlalchemy.orm import Session

import models


def _dependency_context(upstream: models.BridgeJob) -> str:
    """Format the upstream job's completion note (and, if the bridge CLI captured one, a
    `git diff --stat` of what it actually changed -- see BRIDGE_CROSS_REPO_JOBS.md Phase 4) as
    an appendix to the downstream job's prompt, matching the "## heading + --- separator"
    style bridge/jobs.py's other prompt builders already use."""
    lines = ["", "---", f"## Dependency: {upstream.target_repo or 'a prior job'}"]
    if upstream.branch_name:
        lines.append(f"Branch: `{upstream.branch_name}`")
    if upstream.result:
        lines.append("")
        lines.append(upstream.result.strip())
    else:
        lines.append("")
        lines.append(
            "(This job reported no completion note -- check its branch directly for what "
            "actually changed.)"
        )
    if upstream.diff_summary:
        lines.append("")
        lines.append("### Files changed")
        lines.append(f"```\n{upstream.diff_summary.strip()}\n```")
    return "\n".join(lines)


_UPSTREAM_FINISHED_STATUSES = ("done", "needs_confirmation")


def unblock_dependent_jobs(db: Session) -> list[models.BridgeJob]:
    """Transition any "blocked" job whose depends_on_job_id points at a now-finished upstream
    to "pending", appending the upstream job's result to the downstream job's prompt. Finished
    means "done" or "needs_confirmation" -- the latter still means the upstream's actual coding
    work completed, it's only flagged for review because its diff touched a configured
    checkpoint pattern (see app_setting_keys.CHECKPOINT_PATTERNS), which isn't a reason to make
    an unrelated companion job wait. A job blocked on an upstream that ended in "error" or
    "stalled" is left blocked -- unblocking into a run against a broken/incomplete upstream
    needs a human decision, not an automatic one. Self-limiting like check_stale_bridge_jobs:
    once transitioned, a job no longer matches status == "blocked", so calling this repeatedly
    never re-processes the same job twice."""
    blocked_jobs = (
        db.query(models.BridgeJob)
        .filter(
            models.BridgeJob.status == "blocked",
            models.BridgeJob.depends_on_job_id.isnot(None),
        )
        .all()
    )
    if not blocked_jobs:
        return []

    unblocked = []
    for job in blocked_jobs:
        upstream = db.query(models.BridgeJob).filter_by(id=job.depends_on_job_id).first()
        if not upstream or upstream.status not in _UPSTREAM_FINISHED_STATUSES:
            continue
        job.prompt_snapshot = (job.prompt_snapshot or "") + _dependency_context(upstream)
        job.status = "pending"
        unblocked.append(job)

    if unblocked:
        db.commit()
    return unblocked
