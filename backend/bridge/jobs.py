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
        "fix_of_job_id": job.fix_of_job_id,
        "fix_comment_ids": json.loads(job.fix_comment_ids) if job.fix_comment_ids else None,
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
    gets called when the bridge actually picks this up (see run_job's fix_of_job_id branch),
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
        fix_of_job_id=original_job.id,
        fix_comment_ids=json.dumps([c.id for c in comments]),
        created_at=datetime.now(timezone.utc),
    )


def _queue_job_for_card(db: Session, card: models.Card) -> models.BridgeJob:
    """Build (but don't commit) a pending BridgeJob for a card. Caller must
    have already verified card.spec is set."""
    eng_item = None
    if card.external_id:
        eng_item = (
            db.query(models.EngineeringItem)
            .options(selectinload(models.EngineeringItem.comments))
            .filter_by(external_id=card.external_id)
            .first()
        )
    prompt = _build_prompt(card, eng_item)
    return models.BridgeJob(
        card_id=card.id,
        status="pending",
        target_repo=_repo_from_external_id(card.external_id),
        spec_snapshot=card.spec,
        prompt_snapshot=prompt,
        created_at=datetime.now(timezone.utc),
    )
