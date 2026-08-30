"""
Bridge job business logic — building prompts, queueing jobs, serializing
job rows. Kept separate from bridge.router so the router stays a thin HTTP
adapter.
"""
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session, selectinload

import github_sync
import models
import app_setting_keys as setting_keys


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
    }


# Lower means more worth surfacing on a card-tile badge -- error/stalled tie for most urgent
# since both mean "this needs a human," ahead of in-progress, ahead of merely queued/blocked.
_BADGE_STATUS_PRIORITY = {"error": 0, "stalled": 0, "running": 1, "pending": 2, "blocked": 3, "done": 4}


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
