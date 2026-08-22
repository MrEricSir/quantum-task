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
    _job_response,
    _queue_fix_job,
    _queue_job_for_card,
)
from bridge.render import render_agent_script, render_install_script
from bridge.stale import check_stale_bridge_jobs
from deps import get_db, AUTH_PASSWORD
from settings import Settings

router = APIRouter()

_APP_URL = os.getenv("ALLOWED_ORIGIN", "http://localhost:8000")
_OUTPUT_MAX_LINES = 200


# ── Schemas ───────────────────────────────────────────────────────────────────

class _JobCreate(BaseModel):
    card_id: int


class _QueueByTag(BaseModel):
    tag: str


class _JobComplete(BaseModel):
    result: str = ""   # PR link, summary, or empty

class _JobOutput(BaseModel):
    output: str        # chunk of stdout to append

class _JobStart(BaseModel):
    branch: str                        # local branch name created by the bridge
    agent: str                         # hostname of the machine running the job
    worktree_path: str | None = None   # local filesystem path to the job's git worktree


class _JobFix(BaseModel):
    comment_ids: list[int]             # EngineeringItemComment ids to address


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/api/bridge/jobs")
def create_job(body: _JobCreate, db: Session = Depends(get_db)):
    """Queue a bridge job for a card. Card must have a spec."""
    card = db.query(models.Card).filter_by(id=body.card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    if not card.spec:
        raise HTTPException(status_code=400, detail="Card has no spec — generate one first")

    job = _queue_job_for_card(db, card)
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


@router.post("/api/bridge/jobs/{job_id}/fix")
def queue_fix_job(job_id: int, body: _JobFix, db: Session = Depends(get_db)):
    """Queue a "fix" job that resumes job_id's worktree/branch to address specific review
    comments, instead of creating a fresh worktree -- see agent_core.py's run_job() and
    CLAUDE_CODE_INTEGRATION.md's "CodeRabbit feedback integration" plan.

    Validates job_id previously ran and recorded a worktree to resume (worktree_path set) --
    can't validate the worktree still exists ON DISK from here, only the bridge (running
    locally) can do that; see run_job's own error path for what happens if it's since been
    removed via --cleanup."""
    original_job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not original_job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not original_job.worktree_path:
        raise HTTPException(
            status_code=400,
            detail="This job has no recorded worktree to resume -- it may not have run yet, "
                   "or predates worktree tracking.",
        )
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
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _job_response(job)


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


@router.post("/api/bridge/jobs/{job_id}/heartbeat")
def heartbeat_job(job_id: int, db: Session = Depends(get_db)):
    """Bridge pings this periodically while a job is running, so a crashed
    or hung agent process (no output for a long stretch, or an interactive
    session where nothing is posted until it ends) can still be detected
    as stale server-side. See bridge.stale.check_stale_bridge_jobs."""
    job = db.query(models.BridgeJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.post("/api/bridge/jobs/check-stale")
def check_stale_jobs(db: Session = Depends(get_db)):
    """Hit hourly by its own Cloud Scheduler job (see dev.sh's
    gcp_setup_scheduler(), mirroring withings-sync) and by an in-process
    dev loop in main.py -- deliberately independent of the Telegram
    notification sweep in telegram.scheduler.check_all(), which no-ops
    entirely when Telegram isn't configured. The DB transition here must
    happen either way, or the frontend status badge would never update
    for a user who hasn't set up Telegram."""
    stalled = check_stale_bridge_jobs(db)
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


@router.get("/api/bridge/install.py", response_class=PlainTextResponse)
def get_install_script(install_token: str = Query(..., alias="token"), db: Session = Depends(get_db)):
    """
    Serve a pre-authed install script for qtask-bridge.
    Requires ?token=<bridge install token> (separate from AUTH_PASSWORD, shown in the
    GitHub settings modal). The app password and app URL are baked into the served
    script so the CLI can authenticate its own ongoing requests afterward:
        curl https://your-app/api/bridge/install.py?token=... | python3
    """
    valid_install_token = _get_bridge_install_token(db)
    if not secrets.compare_digest(install_token, valid_install_token):
        raise HTTPException(status_code=401, detail="Invalid install token")

    token = AUTH_PASSWORD or ""
    app_url = _APP_URL.rstrip("/")
    script = render_install_script(app_url, token)
    return PlainTextResponse(script, media_type="text/plain")


@router.get("/api/bridge/agent.py", response_class=PlainTextResponse)
def get_agent_script():
    """Serve the qtask-bridge agent script (downloaded by the installer)."""
    return PlainTextResponse(render_agent_script(), media_type="text/plain")
