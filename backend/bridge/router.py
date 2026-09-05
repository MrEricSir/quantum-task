"""
Bridge endpoints — job queue for the local qtask-bridge agent.

Flow:
  1. Frontend POSTs /api/bridge/jobs to queue a job for a card
  2. qtask-bridge polls GET /api/bridge/jobs/next and picks up pending jobs
  3. qtask-bridge POSTs /api/bridge/jobs/{id}/complete when the session ends
  4. Frontend polls GET /api/bridge/jobs/{id} for status updates

Install endpoint:
  GET /api/bridge/install.py — serves a pre-authed install script for qtask-bridge

Business logic lives in bridge.jobs, bridge.render, and bridge.stale.
"""
import base64
import json
import os
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

import models
import app_setting_keys as setting_keys
from bridge.jobs import (
    _build_prompt,
    _get_bridge_install_token,
    _get_bridge_token,
    _job_response,
    _queue_fix_job,
    _queue_job_for_card,
    _queue_resume_job,
    compute_attempt_stats,
    get_bridge_job_statuses,
    get_bridge_jobs_dashboard,
    get_card_job_history,
    get_checkpoint_patterns,
    save_checkpoint_patterns,
    validate_branch_name,
)
from bridge.render import render_agent_script, render_install_script
from bridge.stale import check_stale_bridge_jobs
from bridge.unblock import unblock_dependent_jobs
from deps import get_db
from settings import Settings
from telegram.notify import send_photo

router = APIRouter()

_APP_URL = os.getenv("ALLOWED_ORIGIN", "http://localhost:8000")
_OUTPUT_MAX_LINES = 200


# ── Schemas ───────────────────────────────────────────────────────────────────

class _JobCreate(BaseModel):
    card_id: int
    branch_name: str | None = None     # override for the auto-generated qtask/<id>-<slug> name
    target_repo: str | None = None     # override the repo derived from the card's own GitHub link -- for a cross-repo companion job
    depends_on_job_id: int | None = None   # if set, job starts "blocked" until this job reaches "done"


class _QueueByTag(BaseModel):
    tag: str


class _JobComplete(BaseModel):
    result: str = ""   # PR link, summary, or empty
    diff_summary: str = ""   # `git diff --stat` against the primary branch, complete_job only

class _JobNeedsConfirmation(BaseModel):
    result: str = ""
    diff_summary: str = ""
    matched_paths: list[str] = []   # changed paths that matched a configured checkpoint pattern
    self_review_flagged: bool = False   # True when the automatic self-review pass is why

class _CheckpointPatterns(BaseModel):
    patterns: list[str] = []

class _JobPreview(BaseModel):
    status: str              # starting|running|failed|stopped
    url: str | None = None

class _JobScreenshot(BaseModel):
    image_base64: str

class _JobOutput(BaseModel):
    output: str        # chunk of stdout to append

class _JobStart(BaseModel):
    branch: str                        # local branch name created by the bridge
    agent: str                         # hostname of the machine running the job
    worktree_path: str | None = None   # local filesystem path to the job's git worktree


class _JobFix(BaseModel):
    comment_ids: list[int]             # EngineeringItemComment ids to address


class _JobRenameBranch(BaseModel):
    branch_name: str                   # the new name, already renamed git-side by the CLI


class _JobRequestRename(BaseModel):
    branch_name: str                   # the name the webapp wants the branch renamed to


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/api/bridge/jobs")
def create_job(body: _JobCreate, db: Session = Depends(get_db)):
    """Queue a bridge job for a card. Card must have a spec."""
    card = db.query(models.Card).filter_by(id=body.card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    if not card.spec:
        raise HTTPException(status_code=400, detail="Card has no spec — generate one first")

    branch_name = body.branch_name.strip() if body.branch_name else None
    if branch_name:
        error = validate_branch_name(branch_name)
        if error:
            raise HTTPException(status_code=400, detail=error)

    target_repo = body.target_repo.strip() if body.target_repo else None

    if body.depends_on_job_id is not None:
        upstream_job = db.query(models.BridgeJob).filter_by(id=body.depends_on_job_id).first()
        if not upstream_job:
            raise HTTPException(status_code=404, detail="Dependency job not found")
        if upstream_job.card_id != body.card_id:
            # Not reachable via the web UI today (it only ever passes the current card's own
            # root job id), but a direct API call could otherwise link one card's companion
            # to an unrelated card's job -- which /chain would then pair with the wrong root.
            raise HTTPException(
                status_code=400, detail="Dependency job must belong to the same card"
            )
        existing_companion = (
            db.query(models.BridgeJob)
            .filter(
                models.BridgeJob.depends_on_job_id == body.depends_on_job_id,
                models.BridgeJob.status.in_(["blocked", "pending", "running"]),
            )
            .first()
        )
        if existing_companion:
            # The UI's own state already prevents this in normal single-session use (the
            # "+ Companion job" button disappears once one exists) -- this guards the
            # multi-tab/direct-API-call race, where /chain's "newest wins" would otherwise
            # silently orphan whichever one loses. A companion that's already reached a
            # terminal state (done/error/stalled) doesn't block a fresh one.
            raise HTTPException(
                status_code=409,
                detail=f"Job {body.depends_on_job_id} already has an active companion job "
                       f"(#{existing_companion.id})",
            )

    job = _queue_job_for_card(
        db, card, requested_branch_name=branch_name,
        target_repo=target_repo, depends_on_job_id=body.depends_on_job_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_response(job)


@router.post("/api/bridge/jobs/queue-by-tag")
def queue_jobs_by_tag(body: _QueueByTag, db: Session = Depends(get_db)):
    """Queue a bridge job for every active card with the given tag.

    A card is skipped (not an error) if it has no spec yet, or already has a
    pending/running job. Response reports what was queued and what was
    skipped (and why) so a CLI caller can print a useful summary.
    """
    tag_name = body.tag.strip()
    tag = db.query(models.Tag).filter(models.Tag.name.ilike(tag_name)).first()
    if not tag:
        raise HTTPException(status_code=404, detail=f"Tag '{tag_name}' not found")

    cards = (
        db.query(models.Card)
        .join(models.card_tags, models.Card.id == models.card_tags.c.card_id)
        .filter(
            models.card_tags.c.tag_id == tag.id,
            models.Card.completed == False,   # noqa: E712
            models.Card.archived == False,    # noqa: E712
        )
        .all()
    )

    already_queued_card_ids = {
        row.card_id for row in
        db.query(models.BridgeJob.card_id)
        .filter(models.BridgeJob.status.in_(["pending", "running"]))
        .all()
    }

    queued: list[models.BridgeJob] = []
    skipped_no_spec: list[dict] = []
    skipped_already_queued: list[dict] = []

    for card in cards:
        if card.id in already_queued_card_ids:
            skipped_already_queued.append({"id": card.id, "title": card.title})
            continue
        if not card.spec:
            skipped_no_spec.append({"id": card.id, "title": card.title})
            continue
        job = _queue_job_for_card(db, card)
        db.add(job)
        queued.append(job)

    db.commit()
    for job in queued:
        db.refresh(job)

    return {
        "tag": tag.name,
        "queued": [_job_response(j) for j in queued],
        "skipped_no_spec": skipped_no_spec,
        "skipped_already_queued": skipped_already_queued,
    }


def _get_resumable_job(job_id: int, db: Session) -> models.BridgeJob:
    """Shared validation for /fix and /resume: the job must exist, have a recorded worktree
    to resume (can't validate it still exists ON DISK from here -- only the bridge, running
    locally, can do that; see run_job's own error path for what happens if it's since been
    removed via --cleanup), and not already be pending/running -- resuming a job that's
    still queued or actively being worked would point a second live agent session at the
    exact same worktree, a real git-corruption risk, not just a UX glitch."""
    original_job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not original_job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not original_job.worktree_path:
        raise HTTPException(
            status_code=400,
            detail="This job has no recorded worktree to resume -- it may not have run yet, "
                   "or predates worktree tracking.",
        )
    if original_job.status in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is still {original_job.status} -- wait for it to finish "
                   f"(or stall/error out) before resuming it.",
        )
    return original_job


@router.post("/api/bridge/jobs/{job_id}/fix")
def queue_fix_job(job_id: int, body: _JobFix, db: Session = Depends(get_db)):
    """Queue a "fix" job that resumes job_id's worktree/branch to address specific review
    comments, instead of creating a fresh worktree -- see agent_core.py's run_job() and
    CLAUDE_CODE_INTEGRATION.md's "CodeRabbit feedback integration" plan."""
    original_job = _get_resumable_job(job_id, db)
    if not body.comment_ids:
        raise HTTPException(status_code=400, detail="comment_ids must not be empty")

    comments = (
        db.query(models.EngineeringItemComment)
        .filter(models.EngineeringItemComment.id.in_(body.comment_ids))
        .all()
    )
    found_ids = {c.id for c in comments}
    missing = set(body.comment_ids) - found_ids
    if missing:
        raise HTTPException(status_code=404, detail=f"Comment(s) not found: {sorted(missing)}")

    job = _queue_fix_job(db, original_job, comments)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_response(job)


@router.post("/api/bridge/jobs/{job_id}/resume")
def queue_resume_job(job_id: int, db: Session = Depends(get_db)):
    """Queue a "resume" job that continues job_id's worktree/branch after an interrupted
    session (crash, timeout, disconnect), instead of creating a fresh worktree -- shares
    the exact same resume mechanism as /fix (see agent_core.py's run_job()), just without a
    specific set of comments to address. See CLAUDE_CODE_INTEGRATION.md's "Phase 0" plan."""
    original_job = _get_resumable_job(job_id, db)

    job = _queue_resume_job(db, original_job)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_response(job)


@router.get("/api/bridge/jobs/by-worktree")
def get_latest_worktree_job(path: str, db: Session = Depends(get_db)):
    """Get the latest bridge job for a given worktree_path (for --adopt's running-job guard,
    CLAUDE_CODE_INTEGRATION.md's "Phase 2" plan).

    Keyed by worktree_path rather than card_id/branch_name deliberately: a Phase 1 custom
    branch name isn't reliably parseable back to a card id the way qtask/<id>-<slug> is (see
    _extract_card_id_from_branch's existing degrade-to-None handling in --review), so this
    avoids the same fragility for a check that's specifically meant to be a hard safety
    guard, not best-effort enrichment.

    Registered BEFORE /api/bridge/jobs/{job_id} below -- routes match in registration order,
    and "by-worktree" would otherwise be captured as that route's {job_id} path param and
    fail int conversion (a real 422 hit during this endpoint's own development)."""
    job = (
        db.query(models.BridgeJob)
        .filter_by(worktree_path=path)
        .order_by(models.BridgeJob.created_at.desc())
        .first()
    )
    if not job:
        return {"job": None}
    return {"job": _job_response(job)}


@router.get("/api/bridge/jobs/status")
def get_bridge_job_statuses_endpoint(db: Session = Depends(get_db)):
    """Latest job status per card with a bridge job, for the Board/Today card tile's status
    badge -- polled on an interval independent of any single card being open. Not the Code
    tab's own per-card root+companion chain (see get_card_job_chain below); see
    bridge.jobs.get_bridge_job_statuses's docstring for how the two relate.

    Registered BEFORE /api/bridge/jobs/{job_id} below -- routes match in registration order,
    and "status" would otherwise be captured as that route's {job_id} path param and fail int
    conversion (see by-worktree's docstring above for the same gotcha hit once already)."""
    return {"statuses": get_bridge_job_statuses(db)}


@router.get("/api/bridge/jobs/dashboard")
def get_bridge_jobs_dashboard_endpoint(db: Session = Depends(get_db)):
    """Every currently-relevant bridge job across all cards, for the Engineering page's
    fleet-level dashboard -- see bridge.jobs.get_bridge_jobs_dashboard's docstring for
    exactly what counts as "relevant" and how this differs from /status (per-card badge)
    and /card/{id}/chain (Code tab's own single-card root+companion pairing) below.

    Registered BEFORE /api/bridge/jobs/{job_id} below -- routes match in registration order,
    and "dashboard" would otherwise be captured as that route's {job_id} path param and fail
    int conversion (see by-worktree's docstring above for the same gotcha hit once already)."""
    return {"jobs": get_bridge_jobs_dashboard(db)}


@router.get("/api/bridge/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    """Get status of a single bridge job."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_response(job)


@router.get("/api/bridge/jobs/next/pending")
def get_next_pending(repos: str = Query(None), db: Session = Depends(get_db)):
    """Bridge polls this to pick up the next pending job.

    Optional ?repos=owner/a,owner/b filter — returns jobs whose target_repo matches
    one of the listed repos, or whose target_repo is null (claimable by any bridge).
    """
    query = db.query(models.BridgeJob).filter_by(status="pending")
    if repos:
        repo_list = [r.strip() for r in repos.split(",") if r.strip()]
        if repo_list:
            query = query.filter(
                or_(
                    models.BridgeJob.target_repo.in_(repo_list),
                    models.BridgeJob.target_repo.is_(None),
                )
            )
    job = query.order_by(models.BridgeJob.created_at).first()
    if not job:
        return {"job": None}

    # Always fetch the card — needed for card_title (branch slug) and lazy prompt build
    card = db.query(models.Card).filter_by(id=job.card_id).first()
    if not job.prompt_snapshot and card:
        eng_item = None
        if card.external_id:
            eng_item = (
                db.query(models.EngineeringItem)
                .options(selectinload(models.EngineeringItem.comments))
                .filter_by(external_id=card.external_id)
                .first()
            )
        job.prompt_snapshot = _build_prompt(card, eng_item)

    job.status = "running"
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)

    return {
        "job": {
            **_job_response(job),
            "card_title": card.title if card else "",
            "prompt":     job.prompt_snapshot,
            "spec":       job.spec_snapshot,
        }
    }


@router.post("/api/bridge/jobs/{job_id}/complete")
def complete_job(job_id: int, body: _JobComplete, db: Session = Depends(get_db)):
    """Bridge calls this when the coding agent session ends."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "done"
    job.result = body.result or ""
    job.diff_summary = body.diff_summary or ""
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    # Unblock any cross-repo companion job waiting on this one -- see
    # BRIDGE_CROSS_REPO_JOBS.md Phase 2. Done inline here (not just on the periodic
    # check-stale-adjacent sweep below) so the common case is instant: a companion job
    # becomes claimable the moment its upstream finishes, not up to an hour later.
    unblock_dependent_jobs(db)
    return _job_response(job)


@router.post("/api/bridge/jobs/{job_id}/needs-confirmation")
def job_needs_confirmation(job_id: int, body: _JobNeedsConfirmation, db: Session = Depends(get_db)):
    """Bridge calls this instead of /complete when the session succeeded but either a
    configured checkpoint pattern (app_setting_keys.CHECKPOINT_PATTERNS) matched the diff, or
    the automatic self-review pass (config.toml's self_review) came back flagged -- either
    trigger alone, or both together, means the coding work genuinely finished, it's just
    flagged for review rather than treated as fully resolved. Mirrors complete_job in every
    other respect, including unblocking a waiting companion job, since the upstream work is
    done either way."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "needs_confirmation"
    job.result = body.result or ""
    job.diff_summary = body.diff_summary or ""
    job.checkpoint_matched_paths = json.dumps(body.matched_paths)
    job.self_review_flagged = body.self_review_flagged
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    unblock_dependent_jobs(db)
    return _job_response(job)


@router.post("/api/bridge/jobs/{job_id}/acknowledge")
def acknowledge_job(job_id: int, db: Session = Depends(get_db)):
    """Webapp-only -- flips a needs_confirmation job to done once you've reviewed the flagged
    diff. No CLI/subprocess involvement at all: the coding session already ended, this is
    purely "I looked, it's fine" bookkeeping."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "needs_confirmation":
        raise HTTPException(status_code=400, detail="Job is not awaiting confirmation")
    job.status = "done"
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _job_response(job)


@router.get("/api/bridge/checkpoint-patterns")
def get_checkpoint_patterns_endpoint(db: Session = Depends(get_db)):
    return {"patterns": get_checkpoint_patterns(db)}


@router.put("/api/bridge/checkpoint-patterns")
def set_checkpoint_patterns_endpoint(body: _CheckpointPatterns, db: Session = Depends(get_db)):
    save_checkpoint_patterns(db, body.patterns)
    return {"ok": True}


@router.post("/api/bridge/jobs/{job_id}/error")
def error_job(job_id: int, body: _JobComplete, db: Session = Depends(get_db)):
    """Bridge calls this if the session fails."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "error"
    job.result = body.result or "Unknown error"
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _job_response(job)


@router.post("/api/bridge/jobs/{job_id}/preview")
def update_job_preview(job_id: int, body: _JobPreview, db: Session = Depends(get_db)):
    """Bridge calls this at each auto-preview lifecycle transition (config.toml's
    auto_preview) -- once immediately when the detached launch kicks off (status=starting,
    url=None), once more when the health check confirms it's up (status=running, url=...) or
    status=failed on a health-check timeout / unresolvable open_url; --stop-preview/--cleanup
    call it again with status=stopped. Deliberately independent of the job's own `status` --
    a job can be "done" while its preview keeps running, so this never touches job.status."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.preview_status = body.status
    job.preview_url = body.url
    db.commit()
    return _job_response(job)


@router.post("/api/bridge/jobs/{job_id}/screenshot")
def post_job_screenshot(job_id: int, body: _JobScreenshot, db: Session = Depends(get_db)):
    """Bridge calls this once, right after visual_verify's screenshot capture succeeds
    (config.toml's visual_verify, requires auto_preview -- see agent_core.py's
    _capture_preview_screenshot). Stores the base64 PNG on the job row AND, best-effort,
    forwards it to Telegram inline as a side effect of this state-changing POST -- same
    "inline, not scheduler-polled" posture complete_job/job_needs_confirmation already use for
    unblock_dependent_jobs(db). send_photo already logs its own failures and never raises
    (same posture as send_message), so this never fails the endpoint's own response even if
    the Telegram send itself fails."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.screenshot_data = body.image_base64
    db.commit()

    s = Settings(db)
    if s.telegram_token and s.telegram_chat_id:
        card = db.query(models.Card).filter_by(id=job.card_id).first()
        caption = f"🔗 Preview: {card.title}" if card else "🔗 Preview"
        send_photo(s.telegram_token, s.telegram_chat_id,
                   base64.b64decode(body.image_base64), caption=caption)

    return _job_response(job)


@router.post("/api/bridge/jobs/{job_id}/output")
def post_job_output(job_id: int, body: _JobOutput, db: Session = Depends(get_db)):
    """Bridge posts stdout chunks while the coding agent session is running."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    combined = (job.output or "") + body.output
    lines = combined.splitlines()
    if len(lines) > _OUTPUT_MAX_LINES:
        lines = lines[-_OUTPUT_MAX_LINES:]
    job.output = "\n".join(lines)
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.post("/api/bridge/jobs/{job_id}/start")
def start_job(job_id: int, body: _JobStart, db: Session = Depends(get_db)):
    """Bridge calls this after git setup to record the branch and agent name."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.branch_name    = body.branch
    job.agent_name     = body.agent
    job.worktree_path  = body.worktree_path
    job.updated_at     = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return _job_response(job)


@router.post("/api/bridge/jobs/{job_id}/rename-branch")
def rename_job_branch(job_id: int, body: _JobRenameBranch, db: Session = Depends(get_db)):
    """Bridge calls this after `--rename-branch` has already renamed the actual git branch,
    to correct the DB's record to match. branch_name is otherwise write-once-per-session --
    only /start ever sets it, and only from whatever the CLI resolved at that moment -- so
    nothing else keeps it in sync if the branch gets renamed afterward, whether deliberately
    (forgot to set a name at queue time, or want a better one) or because the user renamed it
    themselves via raw git without going through the CLI."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    branch_name = body.branch_name.strip()
    error = validate_branch_name(branch_name)
    if error:
        raise HTTPException(status_code=400, detail=error)
    job.branch_name = branch_name
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return _job_response(job)


@router.post("/api/bridge/jobs/{job_id}/request-rename")
def request_job_rename(job_id: int, body: _JobRequestRename, db: Session = Depends(get_db)):
    """Ask for an in-progress (or not-yet-started) job's branch to be renamed, from the
    webapp's Code tab. Stores the request in requested_branch_name (the same field a
    queue-time branch override uses) rather than renaming anything directly here:

    - pending job: nothing else to do -- _create_worktree reads requested_branch_name
      fresh when the job actually starts, same as a queue-time override.
    - running job: there's already a worktree/branch, so nothing here can rename it
      directly (the server never reaches into a local machine). The bridge's own
      heartbeat loop (agent_core.py's _start_heartbeat) checks the heartbeat response for
      a requested_branch_name that doesn't match its worktree's actual current branch and
      performs the real `git branch -m` locally, then confirms back via /rename-branch
      above -- so this takes effect on the next heartbeat tick (HEARTBEAT_INTERVAL, 5 min),
      not instantly.

    A job with no active/pending session (done/error/stalled/blocked) has nothing that
    will ever pick this request up, so it's rejected outright rather than silently
    accepting a request that will never be fulfilled."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail="Job is not active — nothing would pick up a rename")
    branch_name = body.branch_name.strip()
    error = validate_branch_name(branch_name)
    if error:
        raise HTTPException(status_code=400, detail=error)
    job.requested_branch_name = branch_name
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return _job_response(job)


@router.post("/api/bridge/jobs/{job_id}/heartbeat")
def heartbeat_job(job_id: int, db: Session = Depends(get_db)):
    """Bridge pings this periodically while a job is running, so a crashed
    or hung agent process (no output for a long stretch, or an interactive
    session where nothing is posted until it ends) can still be detected
    as stale server-side. See bridge.stale.check_stale_bridge_jobs.

    Also returns requested_branch_name so the bridge can notice a rename requested from
    the webapp mid-session -- see request_job_rename above for the full mechanism."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "requested_branch_name": job.requested_branch_name}


@router.post("/api/bridge/jobs/check-stale")
def check_stale_jobs(db: Session = Depends(get_db)):
    """Hit hourly by its own Cloud Scheduler job (see dev.sh's
    gcp_setup_scheduler(), mirroring withings-sync) and by an in-process
    dev loop in main.py -- deliberately independent of the Telegram
    notification sweep in telegram.scheduler.check_all(), which no-ops
    entirely when Telegram isn't configured. The DB transition here must
    happen either way, or the frontend status badge would never update
    for a user who hasn't set up Telegram.

    Also sweeps for cross-repo companion jobs to unblock (BRIDGE_CROSS_REPO_JOBS.md Phase 2)
    -- riding along on this same hourly tick rather than getting its own Cloud Scheduler job,
    since the common case is already handled instantly by complete_job() above; this sweep
    only exists to catch the edge case of a companion job created against an upstream that
    was already done, or the inline hook failing transiently."""
    stalled = check_stale_bridge_jobs(db)
    unblock_dependent_jobs(db)
    notified = 0
    s = Settings(db)
    if stalled and s.telegram_token and s.telegram_chat_id:
        from telegram.scheduler import notify_stalled_jobs
        notified = notify_stalled_jobs(db, s.telegram_token, s.telegram_chat_id, stalled)
    return {"stalled": len(stalled), "notified": notified}


@router.get("/api/bridge/jobs/card/{card_id}/latest")
def get_latest_card_job(card_id: int, db: Session = Depends(get_db)):
    """Get the latest bridge job for a card (for UI status display)."""
    job = (
        db.query(models.BridgeJob)
        .filter_by(card_id=card_id)
        .order_by(models.BridgeJob.created_at.desc())
        .first()
    )
    if not job:
        return {"job": None}
    return {"job": _job_response(job)}


@router.get("/api/bridge/jobs/card/{card_id}/chain")
def get_card_job_chain(card_id: int, db: Session = Depends(get_db)):
    """Get a card's root job plus its cross-repo companion job, if any -- for the Code tab's
    per-job status display (BRIDGE_CROSS_REPO_JOBS.md Phase 3).

    "root" is the newest job for this card with no depends_on_job_id (the original job, or a
    same-repo fix/resume of it -- resumes_job_id is a separate concept from depends_on_job_id,
    so a fix/resume job still counts as "root" here). "companion" is the newest job that
    depends on that root. Only designed for the 2-hop case -- a fix/resume applied to a
    companion job itself (rather than to root) isn't specially represented here, since only
    one companion slot is being designed for in the UI."""
    root = (
        db.query(models.BridgeJob)
        .filter_by(card_id=card_id, depends_on_job_id=None)
        .order_by(models.BridgeJob.created_at.desc())
        .first()
    )
    companion = None
    if root:
        companion = (
            db.query(models.BridgeJob)
            .filter_by(card_id=card_id, depends_on_job_id=root.id)
            .order_by(models.BridgeJob.created_at.desc())
            .first()
        )
    return {
        "root": _job_response(root) if root else None,
        "companion": _job_response(companion) if companion else None,
        "attempts": compute_attempt_stats(db, card_id),
    }


@router.get("/api/bridge/jobs/card/{card_id}/history")
def get_card_job_history_endpoint(card_id: int, db: Session = Depends(get_db)):
    """Every bridge job ever run against this card, newest first -- the plain card detail
    view's Bridge history section, not the Code tab's own current/latest chain above. See
    get_card_job_history's docstring for why this is flat and unfiltered rather than
    root/companion-paired or windowed like /chain and /dashboard."""
    return {"jobs": get_card_job_history(db, card_id)}


@router.get("/api/bridge/repos")
def get_known_repos(db: Session = Depends(get_db)):
    """Known "owner/repo" strings -- for the companion-job repo picker's autocomplete
    (BRIDGE_CROSS_REPO_JOBS.md Phase 3). Not authoritative: config.toml's [repos] table lives
    on the local bridge CLI, not the server, so there's no complete list available here. This
    is a best-effort union of repos the server has actually seen -- from synced GitHub issues/
    PRs, and from repos already used as a job's target_repo -- not a substitute for letting the
    user type any repo string freely."""
    from_items = db.query(models.EngineeringItem.repo).distinct().all()
    from_jobs = (
        db.query(models.BridgeJob.target_repo)
        .filter(models.BridgeJob.target_repo.isnot(None))
        .distinct()
        .all()
    )
    repos = sorted({r for (r,) in from_items} | {r for (r,) in from_jobs})
    return {"repos": repos}


@router.get("/api/bridge/install-token")
def get_bridge_install_token(db: Session = Depends(get_db)):
    return {"token": _get_bridge_install_token(db)}


@router.post("/api/bridge/install-token/rotate")
def rotate_bridge_install_token(db: Session = Depends(get_db)):
    new_token = secrets.token_hex(24)
    row = db.query(models.AppSetting).filter_by(key=setting_keys.BRIDGE_INSTALL_TOKEN).first()
    if row:
        row.value = new_token
    else:
        db.add(models.AppSetting(key=setting_keys.BRIDGE_INSTALL_TOKEN, value=new_token))
    db.commit()
    return {"token": new_token}


@router.post("/api/bridge/token/rotate")
def rotate_bridge_token(db: Session = Depends(get_db)):
    """Rotate the bridge API token, invalidating every already-installed CLI's
    credential. A user re-runs the installer afterward to pick up the new one."""
    new_token = secrets.token_hex(24)
    row = db.query(models.AppSetting).filter_by(key=setting_keys.BRIDGE_TOKEN).first()
    if row:
        row.value = new_token
    else:
        db.add(models.AppSetting(key=setting_keys.BRIDGE_TOKEN, value=new_token))
    db.commit()
    return {"token": new_token}


@router.get("/api/bridge/install.py", response_class=PlainTextResponse)
def get_install_script(install_token: str = Query(..., alias="token"), db: Session = Depends(get_db)):
    """
    Serve a pre-authed install script for qtask-bridge.
    Requires ?token=<bridge install token> (separate from AUTH_PASSWORD, shown in the
    GitHub settings modal). A dedicated, independently-rotatable bridge token (not the
    real app password) and app URL are baked into the served script so the CLI can
    authenticate its own ongoing requests afterward:
        curl https://your-app/api/bridge/install.py?token=... | python3
    """
    valid_install_token = _get_bridge_install_token(db)
    if not secrets.compare_digest(install_token, valid_install_token):
        raise HTTPException(status_code=401, detail="Invalid install token")

    token = _get_bridge_token(db)
    app_url = _APP_URL.rstrip("/")
    script = render_install_script(app_url, token)
    return PlainTextResponse(script, media_type="text/plain")


@router.get("/api/bridge/agent.py", response_class=PlainTextResponse)
def get_agent_script():
    """Serve the qtask-bridge agent script (downloaded by the installer)."""
    return PlainTextResponse(render_agent_script(), media_type="text/plain")
