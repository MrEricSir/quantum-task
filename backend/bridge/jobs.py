"""
Bridge job business logic — building prompts, queueing jobs, serializing
job rows. Kept separate from bridge.router so the router stays a thin HTTP
adapter.
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

import github_sync
import models
import app_setting_keys as setting_keys

# Statuses that should always stay visible on the Engineering page dashboard regardless of
# age, not just "has a live/pending session" -- needs_confirmation has none (the CLI already
# finished and moved on, per _match_checkpoint_patterns) but still belongs here, since a
# flagged job silently aging off the dashboard after 24h would defeat the point of flagging
# it. Purely a dashboard-visibility/sort-order concern: GET /api/bridge/jobs/next/pending
# (the CLI's actual queue-progression query) filters on status == "pending" directly and
# never consults this list, so a status's presence/absence here has no effect on whether the
# CLI treats it as "move on to the next job." See get_bridge_jobs_dashboard.
_ACTIVE_JOB_STATUSES = ("pending", "running", "blocked", "needs_confirmation")
_DASHBOARD_RECENT_WINDOW = timedelta(hours=24)


def _claim_job_if_still_pending(db: Session, job_id: int) -> bool:
    """Atomically flips one job pending -> running, conditioned on it still being
    pending at the moment this UPDATE actually runs -- not just when it was read.

    Two bridge instances (or two overlapping --watch/--tag/--card invocations) can
    both SELECT the same pending job before either commits; without a WHERE guard
    on the write itself, both would flip it to running and both would start work
    on it. The `status="pending"` condition here makes the write a no-op (rowcount
    0) for whichever caller loses the race, so GET /api/bridge/jobs/next/pending
    can fall through to the next candidate instead of double-claiming."""
    updated = (
        db.query(models.BridgeJob)
        .filter_by(id=job_id, status="pending")
        .update({"status": "running", "updated_at": datetime.now(timezone.utc)})
    )
    db.commit()
    return updated > 0


def get_checkpoint_patterns(db: Session) -> list[str]:
    """Return the configured checkpoint glob patterns. Same newline-joined storage
    convention as github_sync.get_config's repos list. Empty list (the default) means
    the checkpoint gate never fires -- opt-in, no config change required for existing
    installs."""
    row = db.query(models.AppSetting).filter_by(key=setting_keys.CHECKPOINT_PATTERNS).first()
    return [p.strip() for p in row.value.splitlines() if p.strip()] if row and row.value else []


def save_checkpoint_patterns(db: Session, patterns: list[str]) -> None:
    value = "\n".join(patterns)
    row = db.query(models.AppSetting).filter_by(key=setting_keys.CHECKPOINT_PATTERNS).first()
    if row:
        row.value = value
    else:
        db.add(models.AppSetting(key=setting_keys.CHECKPOINT_PATTERNS, value=value))
    db.commit()


def _get_bridge_install_token(db: Session) -> str:
    """Return the current bridge install token, creating one if it doesn't exist.

    This is deliberately separate from AUTH_PASSWORD: it only gates the one-time
    curl-able install script, so it can be shared or rotated without touching the
    real app password (mirrors calendar.py's _get_export_token)."""
    row = db.query(models.AppSetting).filter_by(key=setting_keys.BRIDGE_INSTALL_TOKEN).first()
    if row:
        return row.value
    token = secrets.token_hex(24)
    db.add(models.AppSetting(key=setting_keys.BRIDGE_INSTALL_TOKEN, value=token))
    db.commit()
    return token


def _get_bridge_token(db: Session) -> str:
    """Return the current bridge API token, creating one if it doesn't exist.

    This is the credential baked into the served install script for the CLI's own
    ongoing requests -- deliberately separate from AUTH_PASSWORD (see BRIDGE_TOKEN's
    definition in app_setting_keys.py) and from BRIDGE_INSTALL_TOKEN (which only
    gates fetching the install script itself, not the CLI's later requests)."""
    row = db.query(models.AppSetting).filter_by(key=setting_keys.BRIDGE_TOKEN).first()
    if row:
        return row.value
    token = secrets.token_hex(24)
    db.add(models.AppSetting(key=setting_keys.BRIDGE_TOKEN, value=token))
    db.commit()
    return token


def validate_branch_name(branch_name: str) -> str | None:
    """Return an error message if branch_name is unsafe to hand to git/the filesystem,
    else None. Not full git check-ref-format compliance -- just the characters that
    matter for how this branch name gets used downstream: passed as its own argv
    element to `git worktree add ... -b <name>` (never through a shell, so no shell
    injection risk) and joined into a worktree directory path
    (bridge/scripts/agent_core.py's WORKTREES_ROOT/repo_slug/branch.replace("/", "-")).
    ".." is currently blocked indirectly by git's own ref-name rules before a branch
    with that name could ever exist, but validating it here too means that stays true
    even if a future code path builds a path from this string before git ever sees it."""
    if not branch_name:
        return "Branch name can't be empty"
    if any(c.isspace() for c in branch_name):
        return "Branch name can't contain whitespace"
    if branch_name.startswith("-"):
        return "Branch name can't start with '-'"
    if ".." in branch_name:
        return "Branch name can't contain '..'"
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in branch_name):
        return "Branch name can't contain control characters"
    if any(c in branch_name for c in ("~", "^", ":", "?", "*", "[", "\\")):
        return "Branch name can't contain any of: ~ ^ : ? * [ \\"
    if branch_name.startswith("/") or branch_name.endswith("/"):
        return "Branch name can't start or end with '/'"
    return None


def _repo_from_external_id(external_id: str | None) -> str | None:
    """Parse 'github:owner/repo/issues/42' → 'owner/repo', or None if not a GitHub link.

    Delegates to github_sync's regex-validated parser rather than duplicating
    a looser string-split version -- this repo previously had two independent
    parsers for the same format that could silently drift apart."""
    if not external_id:
        return None
    parsed = github_sync._parse_external_id(external_id)
    return f"{parsed[0]}/{parsed[1]}" if parsed else None


def _build_prompt(card: models.Card, eng_item: models.EngineeringItem | None) -> str:
    """Compile the full task prompt from card spec + GitHub context."""
    lines = [f"# Feature: {card.title}"]
    if eng_item:
        lines.append(f"Source: {eng_item.url}")
    lines.append("")

    if card.spec:
        lines.append(card.spec)
        lines.append("")

    if eng_item:
        lines.append("---")
        kind = "PR" if eng_item.item_type == "pr" else "Issue"
        lines.append(f"## GitHub {kind}: {eng_item.repo}#{eng_item.number}")
        if eng_item.body:
            lines.append("")
            lines.append(eng_item.body)
        if eng_item.comments:
            lines.append("")
            lines.append("### Comments")
            for c in eng_item.comments:
                lines.append(f"\n**{c.author}**: {c.body}")

    if card.description and card.description.strip():
        lines.append("")
        lines.append("---")
        lines.append("## Developer Notes")
        lines.append(card.description.strip())

    return "\n".join(lines)


def _build_fix_prompt(card: models.Card, comments: list["models.EngineeringItemComment"]) -> str:
    """Compile a fix-job prompt scoped to specific review comments -- framed as "apply these
    fixes," not a general feature spec, per CLAUDE_CODE_INTEGRATION.md's "CodeRabbit feedback
    integration" plan. Paired with agent_core.py's _make_fix_prompt, which wraps this same
    written content with "apply the specific fixes... not a general invitation to refactor"
    instructions at launch time."""
    lines = [f"# Apply review feedback: {card.title}", ""]
    lines.append(
        "Address each of the following review comments. Only make the changes they "
        "describe -- this is not a general invitation to refactor."
    )
    for c in comments:
        lines.append("")
        lines.append("---")
        location = f"{c.diff_path}:{c.diff_line}" if c.diff_path else None
        header = f"## {c.author}" + (f" — {location}" if location else "")
        lines.append(header)
        lines.append("")
        lines.append(c.body)
    return "\n".join(lines)


def _build_resume_prompt(card: models.Card, eng_item: models.EngineeringItem | None) -> str:
    """Compile a resume-job prompt for continuing an interrupted session -- same underlying
    spec/GitHub context as a normal job's _build_prompt, with a prepended note that this is a
    fresh agent process picking up EXISTING progress in the worktree, not a fresh start. Paired
    with agent_core.py's _make_resume_prompt, which wraps this same written content with
    "check git log/diff before continuing" instructions at launch time. See
    CLAUDE_CODE_INTEGRATION.md's "Phase 0" plan."""
    preamble = (
        "# Resuming an interrupted session\n\n"
        "You previously started this task in this exact worktree and branch, and the prior "
        "session ended before finishing it (crash, timeout, or disconnect) -- this is a fresh "
        "agent process, but the worktree already has whatever progress was made. Run `git "
        "log` and review `git diff` against the base branch first to see what's already "
        "committed, then continue toward the goal below rather than starting over.\n\n---\n"
    )
    return preamble + _build_prompt(card, eng_item)


_OUTPUT_MAX_LINES = 200


def _job_response(job: models.BridgeJob) -> dict:
    return {
        "id":            job.id,
        "card_id":       job.card_id,
        "status":        job.status,
        "target_repo":   job.target_repo,
        "branch_name":   job.branch_name,
        "agent_name":    job.agent_name,
        "worktree_path": job.worktree_path,
        "result":        job.result,
        "output":        job.output,
        "spec_snapshot": job.spec_snapshot,
        "created_at":    job.created_at.isoformat(),
        "updated_at":    job.updated_at.isoformat() if job.updated_at else None,
        "resumes_job_id": job.resumes_job_id,
        "fix_comment_ids": json.loads(job.fix_comment_ids) if job.fix_comment_ids else None,
        "requested_branch_name": job.requested_branch_name,
        "depends_on_job_id": job.depends_on_job_id,
        "diff_summary": job.diff_summary,
        "checkpoint_matched_paths": json.loads(job.checkpoint_matched_paths) if job.checkpoint_matched_paths else None,
        "self_review_flagged": job.self_review_flagged,
        "preview_status": job.preview_status,
        "preview_url": job.preview_url,
        "screenshot_data": job.screenshot_data,
    }


# Lower means more worth surfacing on a card-tile badge -- error/stalled tie for most urgent
# since both mean "this needs a human," ahead of in-progress, ahead of merely queued/blocked.
_BADGE_STATUS_PRIORITY = {
    "error": 0, "stalled": 0, "needs_confirmation": 0,
    "running": 1, "pending": 2, "blocked": 3, "done": 4,
}


def get_bridge_job_statuses(db: Session) -> dict[int, dict]:
    """Return the current job status per card, for the Board/Today card tile's at-a-glance
    status badge.

    Mirrors get_card_job_chain's root+companion pairing in bridge/router.py (newest root job,
    newest companion of *that specific* root -- not just any job ever attached to the card, so
    a card whose very first attempt errored but was later resumed successfully doesn't show a
    stale error forever) but batched across every card with a job in two queries instead of
    one query per card, and collapses root+companion down to whichever single status is more
    worth surfacing rather than returning both -- this is a lightweight indicator, not the
    authoritative per-job display the Code tab itself renders.

    Selects only the columns needed (not spec_snapshot/prompt_snapshot/output, which can be
    large) since this runs on a poll interval across every card with a job, unlike the Code
    tab's own once-per-open-card chain fetch.
    """
    root_rows = (
        db.query(models.BridgeJob.id, models.BridgeJob.card_id, models.BridgeJob.status)
        .filter(models.BridgeJob.depends_on_job_id.is_(None))
        .order_by(models.BridgeJob.card_id, models.BridgeJob.created_at.desc())
        .all()
    )
    root_by_card: dict[int, tuple[int, str]] = {}  # card_id -> (job_id, status)
    for job_id, card_id, status in root_rows:
        if card_id not in root_by_card:
            root_by_card[card_id] = (job_id, status)

    companion_rows = (
        db.query(models.BridgeJob.depends_on_job_id, models.BridgeJob.status)
        .filter(models.BridgeJob.depends_on_job_id.isnot(None))
        .order_by(models.BridgeJob.depends_on_job_id, models.BridgeJob.created_at.desc())
        .all()
    )
    companion_by_root: dict[int, str] = {}  # root_job_id -> newest companion's status
    for root_id, status in companion_rows:
        if root_id not in companion_by_root:
            companion_by_root[root_id] = status

    statuses: dict[int, dict] = {}
    for card_id, (job_id, root_status) in root_by_card.items():
        companion_status = companion_by_root.get(job_id)
        if (
            companion_status
            and _BADGE_STATUS_PRIORITY.get(companion_status, 5) < _BADGE_STATUS_PRIORITY.get(root_status, 5)
        ):
            statuses[card_id] = {"job_id": job_id, "status": companion_status}
        else:
            statuses[card_id] = {"job_id": job_id, "status": root_status}
    return statuses


def get_bridge_jobs_dashboard(db: Session) -> list[dict]:
    """Every currently-relevant bridge job across all cards, for the Engineering page's
    dashboard -- a fleet-level view, unlike the Code tab's own single-card chain or the
    card-tile badge's single-status-per-card summary (see get_bridge_job_statuses).

    "Relevant" = has a live/pending session (pending/running/blocked, shown regardless of
    age) or finished within the last 24h (done/error/stalled) -- a build that just
    finished or errored stays visible without hunting, but ancient history doesn't
    clutter the page forever. Sorted active-first, then most-recently-updated, so
    in-progress work always sits above historical record even if its last heartbeat is
    older than a job that just finished.

    Excludes the large text columns (spec_snapshot/prompt_snapshot/output) -- this is a
    list across every relevant job, not a single job's detail view."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - _DASHBOARD_RECENT_WINDOW
    last_activity = func.coalesce(models.BridgeJob.updated_at, models.BridgeJob.created_at)
    jobs = (
        db.query(models.BridgeJob)
        .filter(
            (models.BridgeJob.status.in_(_ACTIVE_JOB_STATUSES)) | (last_activity >= cutoff)
        )
        .order_by(last_activity.desc())
        .limit(30)
        .all()
    )
    if not jobs:
        return []

    card_titles = dict(
        db.query(models.Card.id, models.Card.title)
        .filter(models.Card.id.in_({j.card_id for j in jobs}))
        .all()
    )
    jobs.sort(key=lambda j: j.status not in _ACTIVE_JOB_STATUSES)  # stable: keeps recency order within each group

    return [
        {
            "id": job.id,
            "card_id": job.card_id,
            "card_title": card_titles.get(job.card_id, "(deleted card)"),
            "status": job.status,
            "target_repo": job.target_repo,
            "branch_name": job.branch_name,
            "agent_name": job.agent_name,
            "result": job.result,
            "depends_on_job_id": job.depends_on_job_id,
            "resumes_job_id": job.resumes_job_id,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "preview_status": job.preview_status,
            "preview_url": job.preview_url,
            "has_screenshot": job.screenshot_data is not None,
        }
        for job in jobs
    ]


def get_card_job_history(db: Session, card_id: int) -> list[dict]:
    """Every bridge job ever run against this card, newest first -- for the plain card
    detail view's Bridge history section, not the Code tab's own current/latest chain
    (get_card_job_chain above) or the Engineering page's fleet-wide dashboard
    (get_bridge_jobs_dashboard above).

    Deliberately flat and unfiltered, unlike both of those:
    - No depends_on_job_id split into root/companion -- get_card_job_chain's root+companion
      pairing answers "what's the CURRENT status," a different question from "what happened
      over time." A cross-repo companion job did real work for this card (shares its spec),
      so it's included inline here, not grouped away or excluded; the frontend can label a
      row as a companion from its own target_repo/depends_on_job_id.
    - No _ACTIVE_JOB_STATUSES/24h recency filter and no .limit() -- get_bridge_jobs_dashboard's
      windowing exists so a FLEET view across every card doesn't get cluttered with ancient
      history; a per-card history is already scoped to one card, and showing old jobs is the
      whole point.
    - No card-existence check -- returns [] for a card with no jobs (or an unknown card_id),
      mirroring get_latest_card_job/get_card_job_chain's existing "200 + empty, never 404"
      convention for this same family of per-card endpoints.

    Builds its own explicit dict (like get_bridge_jobs_dashboard does) rather than reusing/
    filtering _job_response() -- that function backs single-job-detail endpoints and may grow
    fields for those use cases later; a list endpoint should enumerate what it selects so a
    future large field doesn't silently leak into every row of a list. Excludes
    spec_snapshot/prompt_snapshot/output/prompt (single-job-detail-only, and prompt/spec text
    can be large), card_id/card_title (the caller already knows which card this is), and the
    full screenshot_data blob (a `has_screenshot` boolean stands in for it -- base64 image
    data in a list of rows would be the largest field here by far)."""
    jobs = (
        db.query(models.BridgeJob)
        .filter_by(card_id=card_id)
        .order_by(models.BridgeJob.created_at.desc())
        .all()
    )
    return [
        {
            "id": job.id,
            "status": job.status,
            "target_repo": job.target_repo,
            "branch_name": job.branch_name,
            "agent_name": job.agent_name,
            "result": job.result,
            "diff_summary": job.diff_summary,
            "checkpoint_matched_paths": json.loads(job.checkpoint_matched_paths) if job.checkpoint_matched_paths else None,
            "self_review_flagged": job.self_review_flagged,
            "preview_status": job.preview_status,
            "preview_url": job.preview_url,
            "has_screenshot": bool(job.screenshot_data),
            "depends_on_job_id": job.depends_on_job_id,
            "resumes_job_id": job.resumes_job_id,
            "fix_comment_ids": json.loads(job.fix_comment_ids) if job.fix_comment_ids else None,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
        for job in jobs
    ]


def compute_attempt_stats(db: Session, card_id: int) -> dict | None:
    """How many times this card's primary job lineage has been attempted, and how many of
    the attempts before the current (latest) one failed -- the data behind the Code tab's
    "3rd attempt -- 2 previous attempts failed" line, so a repeated failure loop is visible
    instead of a Resume button that looks the same on attempt 1 and attempt 5.

    "Attempt" = a root job (depends_on_job_id IS NULL, same definition get_card_job_chain
    uses) -- a fresh run or a resume/fix of one, in chronological order. Cross-repo companion
    jobs aren't attempts at this card's own work, so they're excluded, same as
    get_card_job_chain's "root" already does. "Failed" = status is error or stalled;
    "cancelled"-style states don't exist in this app's status vocabulary, only those two mean
    the session didn't reach a working result.

    Returns None if the card has no bridge jobs at all yet (nothing to report)."""
    statuses = [
        s for (s,) in (
            db.query(models.BridgeJob.status)
            .filter_by(card_id=card_id, depends_on_job_id=None)
            .order_by(models.BridgeJob.created_at.asc())
            .all()
        )
    ]
    if not statuses:
        return None
    prior_statuses = statuses[:-1]
    return {
        "number": len(statuses),
        "prior_count": len(prior_statuses),
        "prior_failed_count": sum(1 for s in prior_statuses if s in ("error", "stalled")),
    }


def _queue_fix_job(
    db: Session, original_job: models.BridgeJob, comments: list[models.EngineeringItemComment]
) -> models.BridgeJob:
    """Build (but don't commit) a pending "fix" BridgeJob that resumes original_job's
    worktree/branch instead of creating a fresh one -- see agent_core.py's run_job() and
    CLAUDE_CODE_INTEGRATION.md's "CodeRabbit feedback integration" plan. Caller
    (bridge.router) must have already verified original_job.worktree_path is set (i.e. it
    previously ran and has something to resume) and that comments is non-empty.

    branch_name/worktree_path are copied from original_job now, at creation time, rather
    than left null for the bridge to fill in via /start the way a normal job's are -- we
    already know them, no reason to make the bridge re-derive or re-fetch them. /start still
    gets called when the bridge actually picks this up (see run_job's resumes_job_id branch),
    to record which agent/machine ran it and refresh updated_at, same as every other job
    kind -- it just echoes back values that were already set instead of establishing them
    for the first time."""
    card = db.query(models.Card).filter_by(id=original_job.card_id).first()
    prompt = _build_fix_prompt(card, comments)
    return models.BridgeJob(
        card_id=original_job.card_id,
        status="pending",
        target_repo=original_job.target_repo,
        branch_name=original_job.branch_name,
        worktree_path=original_job.worktree_path,
        prompt_snapshot=prompt,
        resumes_job_id=original_job.id,
        fix_comment_ids=json.dumps([c.id for c in comments]),
        created_at=datetime.now(timezone.utc),
    )


def _queue_resume_job(db: Session, original_job: models.BridgeJob) -> models.BridgeJob:
    """Build (but don't commit) a pending "resume" BridgeJob that continues original_job's
    worktree/branch after an interrupted session, instead of creating a fresh one -- shares
    the exact same resumes_job_id-driven resume mechanism in agent_core.py's run_job() that
    _queue_fix_job uses, distinguished from a fix job by fix_comment_ids being left unset (no
    specific comments to address, just "keep going"). See CLAUDE_CODE_INTEGRATION.md's
    "Phase 0" plan. Caller (bridge.router) must have already verified original_job.worktree_path
    is set.

    branch_name/worktree_path copied from original_job at creation time, same reasoning as
    _queue_fix_job."""
    card = db.query(models.Card).filter_by(id=original_job.card_id).first()
    eng_item = None
    if card.external_id:
        eng_item = (
            db.query(models.EngineeringItem)
            .options(selectinload(models.EngineeringItem.comments))
            .filter_by(external_id=card.external_id)
            .first()
        )
    prompt = _build_resume_prompt(card, eng_item)
    return models.BridgeJob(
        card_id=original_job.card_id,
        status="pending",
        target_repo=original_job.target_repo,
        branch_name=original_job.branch_name,
        worktree_path=original_job.worktree_path,
        prompt_snapshot=prompt,
        resumes_job_id=original_job.id,
        created_at=datetime.now(timezone.utc),
    )


def _queue_job_for_card(
    db: Session, card: models.Card, requested_branch_name: str | None = None,
    target_repo: str | None = None, depends_on_job_id: int | None = None,
) -> models.BridgeJob:
    """Build (but don't commit) a pending (or, if depends_on_job_id is given, blocked)
    BridgeJob for a card. Caller must have already verified card.spec is set (and, if
    requested_branch_name is given, that it's already been sanity-checked -- this function
    trusts its caller, same as everywhere else in this module).

    target_repo overrides the repo normally derived from the card's own GitHub link -- used
    for a cross-repo companion job, which targets a different repo than the card's
    external_id points at while still sharing the card's spec/GitHub context for overall
    feature intent. See BRIDGE_CROSS_REPO_JOBS.md."""
    eng_item = None
    if card.external_id:
        eng_item = (
            db.query(models.EngineeringItem)
            .options(selectinload(models.EngineeringItem.comments))
            .filter_by(external_id=card.external_id)
            .first()
        )
    prompt = _build_prompt(card, eng_item)
    if target_repo:
        # A companion job shares the card's spec verbatim (built above) with whatever other
        # repo's job, so it needs to know it's only responsible for one slice of it -- without
        # this, both jobs would just see the same "add an endpoint and wire up the UI" brief
        # with no indication which half is theirs.
        prompt = (
            f"You are working in the {target_repo} repo. The brief below may describe "
            f"changes spanning more than one repo -- only implement the part that belongs in "
            f"THIS repo.\n\n---\n\n" + prompt
        )
    return models.BridgeJob(
        card_id=card.id,
        status="blocked" if depends_on_job_id else "pending",
        target_repo=target_repo or _repo_from_external_id(card.external_id),
        spec_snapshot=card.spec,
        prompt_snapshot=prompt,
        requested_branch_name=requested_branch_name,
        depends_on_job_id=depends_on_job_id,
        created_at=datetime.now(timezone.utc),
    )
