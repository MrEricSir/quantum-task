"""
Bridge endpoints — job queue for the local qtask-bridge agent.

Flow:
  1. Frontend POSTs /api/bridge/jobs to queue a job for a card
  2. qtask-bridge polls GET /api/bridge/jobs/next and picks up pending jobs
  3. qtask-bridge POSTs /api/bridge/jobs/{id}/complete when the session ends
  4. Frontend polls GET /api/bridge/jobs/{id} for status updates

Install endpoint:
  GET /api/bridge/install.py — serves a pre-authed install script for qtask-bridge
"""
import os
import secrets
import textwrap
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

import models
import app_setting_keys as setting_keys
from deps import get_db, AUTH_PASSWORD

router = APIRouter()

_APP_URL = os.getenv("ALLOWED_ORIGIN", "http://localhost:8000")


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
    branch: str        # local branch name created by the bridge
    agent: str         # hostname of the machine running the job


# ── Helpers ───────────────────────────────────────────────────────────────────

def _repo_from_external_id(external_id: str | None) -> str | None:
    """Parse 'github:owner/repo/issues/42' → 'owner/repo', or None if not a GitHub link."""
    if not external_id or not external_id.startswith("github:"):
        return None
    path = external_id[len("github:"):]   # "owner/repo/issues/42"
    parts = path.split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else None


def _build_prompt(card: models.Card, eng_item: models.EngineeringItem | None) -> str:
    """Compile the full Claude Code prompt from card spec + GitHub context."""
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


_OUTPUT_MAX_LINES = 200


def _job_response(job: models.BridgeJob) -> dict:
    return {
        "id":            job.id,
        "card_id":       job.card_id,
        "status":        job.status,
        "target_repo":   job.target_repo,
        "branch_name":   job.branch_name,
        "agent_name":    job.agent_name,
        "result":        job.result,
        "output":        job.output,
        "spec_snapshot": job.spec_snapshot,
        "created_at":    job.created_at.isoformat(),
        "updated_at":    job.updated_at.isoformat() if job.updated_at else None,
    }


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
    """Bridge calls this when the Claude Code session ends."""
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
    """Bridge posts stdout chunks while the Claude Code session is running."""
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
    job.branch_name = body.branch
    job.agent_name  = body.agent
    job.updated_at  = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return _job_response(job)


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

    script = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        \"\"\"
        qtask-bridge installer
        Installs the qtask-bridge CLI with your app URL and token pre-configured.
        \"\"\"
        import os, sys, stat, ssl, textwrap, urllib.error, urllib.request, json

        APP_URL = "{app_url}"
        TOKEN   = "{token}"
        INSTALL_DIR = os.path.expanduser("~/.local/bin")
        CONFIG_DIR  = os.path.expanduser("~/.config/qtask-bridge")
        CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
        TOML_FILE   = os.path.join(CONFIG_DIR, "claude.toml")
        BRIDGE_PATH = os.path.join(INSTALL_DIR, "qtask-bridge")

        # Indented consistently with the rest of this script (unlike a
        # column-0 literal) so textwrap.dedent() actually has something
        # uniform to strip — both here at generation time and again below
        # at install time, when it's re-dedented back down to column 0
        # before being written to claude.toml.
        TOML_TEMPLATE = textwrap.dedent(\"\"\"\\
            # qtask-bridge configuration

            # Friendly name shown in notifications and the job status panel.
            # Defaults to your hostname if left empty.
            name = ""

            # Map repo slugs to local checkout paths. Either a plain path string,
            # or a table with "path" and an optional "setup_cmd" — a one-time
            # command run in a fresh worktree before Claude launches, for repos
            # that need dependencies installed (npm install, pip install, etc).
            #
            # [repos]
            # "owner/myapp" = "/Users/you/code/myapp"
            #
            # [repos."owner/api"]
            # path = "/Users/you/code/api"
            # setup_cmd = "npm install"

            # Fallback setup_cmd used for any repo above that doesn't set its own.
            #
            # setup_cmd = "npm install"

            # Alternatively, list root directories and the bridge will discover repos by
            # scanning for matching .git remotes automatically. Auto-discovered repos
            # don't get a setup_cmd — configure those explicitly under [repos] instead.
            #
            # repo_roots = ["~/code", "~/work"]
        \"\"\")

        def main():
            # Download the bridge script from the app
            print("Downloading qtask-bridge...")
            req = urllib.request.Request(
                f"{{APP_URL}}/api/bridge/agent.py",
                headers={{"Authorization": f"Bearer {{TOKEN}}"}},
            )
            try:
                with urllib.request.urlopen(req) as r:
                    script_content = r.read()
            except urllib.error.URLError as e:
                if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                    cert_cmd = (
                        "/Applications/Python " + str(sys.version_info.major) + "."
                        + str(sys.version_info.minor) + "/Install Certificates.command"
                    )
                    print()
                    print("SSL certificate verification failed while downloading qtask-bridge.")
                    print("This is a known issue with the official python.org installer on macOS --")
                    print("it doesn't come with a CA certificate bundle until you run its one-time")
                    print("setup script. Fix it, then re-run this installer:")
                    print()
                    print("    open '" + cert_cmd + "'")
                    print()
                    sys.exit(1)
                raise

            os.makedirs(INSTALL_DIR, exist_ok=True)
            os.makedirs(CONFIG_DIR,  exist_ok=True)

            with open(BRIDGE_PATH, "wb") as f:
                f.write(script_content)
            os.chmod(BRIDGE_PATH, os.stat(BRIDGE_PATH).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

            config = {{"app_url": APP_URL, "token": TOKEN}}
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)

            # Write config.toml only if it doesn't exist — preserve user edits on reinstall
            if not os.path.exists(TOML_FILE):
                with open(TOML_FILE, "w") as f:
                    f.write(TOML_TEMPLATE)
                print(f"Created:   {{TOML_FILE}}")
            else:
                print(f"Kept:      {{TOML_FILE}}  (already exists)")

            print(f"Installed: {{BRIDGE_PATH}}")
            print(f"Config:    {{CONFIG_FILE}}")

            # Add ~/.local/bin to PATH if needed
            path_export = 'export PATH="$HOME/.local/bin:$PATH"'
            if INSTALL_DIR not in os.environ.get("PATH", "").split(os.pathsep):
                shell = os.environ.get("SHELL", "")
                if "zsh" in shell:
                    rc = os.path.expanduser("~/.zshrc")
                elif "bash" in shell:
                    rc = os.path.expanduser("~/.bash_profile")
                else:
                    rc = None
                if rc:
                    try:
                        existing = open(rc).read() if os.path.exists(rc) else ""
                        if ".local/bin" not in existing:
                            with open(rc, "a") as rf:
                                rf.write("\\n# Added by qtask-bridge installer\\n" + path_export + "\\n")
                            print("Added ~/.local/bin to PATH in " + rc)
                            print("Run: source " + rc + "  (or open a new terminal)")
                    except OSError as e:
                        print("Could not update " + rc + ": " + str(e))
                        print("Add manually: " + path_export)
                else:
                    print("\\nAdd to your shell config: " + path_export)

            print()
            print("Usage (run from your repo directory):")
            print("  qtask-bridge --card <card-id>   # run a specific card's job")
            print("  qtask-bridge --watch            # poll for jobs automatically")
            print("  qtask-bridge --tag <name>       # run every pending-spec card with this tag")
            print("  qtask-bridge --cleanup          # list/remove finished qtask worktrees")

        main()
    """)
    return PlainTextResponse(script, media_type="text/plain")


@router.get("/api/bridge/agent.py", response_class=PlainTextResponse)
def get_agent_script():
    """Serve the qtask-bridge agent script (downloaded by the installer)."""
    script = textwrap.dedent("""\
        #!/usr/bin/env python3
        \"\"\"
        qtask-bridge — Claude Code bridge agent for the todo app.

        Usage:
          qtask-bridge --card <id>   Fetch job for a card and launch Claude Code
          qtask-bridge --watch       Poll for pending jobs and handle them automatically
          qtask-bridge --tag <name>  Queue + run every pending-spec card with this tag,
                                      sequentially and unattended, each in its own git worktree
          qtask-bridge --cleanup     List and optionally remove finished qtask worktrees

        Config files in ~/.config/qtask-bridge/:
          config.json  — app URL and auth token (written by installer)
          claude.toml  — repo mappings and agent name (edit to configure multi-repo)
        \"\"\"
        import argparse
        import json
        import os
        import re
        import socket
        import subprocess
        import sys
        import threading
        import time
        import urllib.parse
        import urllib.request
        import urllib.error
        try:
            import tomllib
        except ModuleNotFoundError:
            tomllib = None

        CONFIG_DIR  = os.path.expanduser("~/.config/qtask-bridge")
        CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
        TOML_FILE   = os.path.join(CONFIG_DIR, "claude.toml")
        POLL_INTERVAL = 30        # seconds between polls in --watch mode
        OUTPUT_FLUSH_INTERVAL = 5 # seconds between output POSTs while streaming
        OUTPUT_FLUSH_LINES = 20   # flush after this many lines even if interval not reached
        SPEC_FILENAME = "BRIDGE_SPEC.md"
        GITIGNORE_ENTRY = "BRIDGE_SPEC.md\\n"
        WORKTREES_ROOT = os.path.expanduser("~/.local/share/qtask-bridge/worktrees")


        def load_config():
            if not os.path.exists(CONFIG_FILE):
                print(f"Config not found: {CONFIG_FILE}", file=sys.stderr)
                print("Re-run the installer or create the config manually.", file=sys.stderr)
                sys.exit(1)
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            cfg.setdefault("name", None)
            cfg.setdefault("repos", {})
            cfg.setdefault("repo_roots", [])
            if tomllib and os.path.exists(TOML_FILE):
                try:
                    with open(TOML_FILE, "rb") as f:
                        toml = tomllib.load(f)
                    if toml.get("name"):
                        cfg["name"] = toml["name"]
                    cfg["repos"]      = dict(toml.get("repos") or {})
                    cfg["repo_roots"] = list(toml.get("repo_roots") or [])
                except Exception as e:
                    print(f"[bridge] Warning: could not parse {TOML_FILE}: {e}", file=sys.stderr)
            return cfg


        def api(cfg, method, path, body=None):
            url = cfg["app_url"].rstrip("/") + path
            data = json.dumps(body).encode() if body is not None else None
            headers = {
                "Authorization": f"Bearer {cfg['token']}",
                "Content-Type": "application/json",
            }
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                print(f"API error {e.code}: {body}", file=sys.stderr)
                return None


        def ensure_gitignore(spec_path):
            gitignore = os.path.join(os.path.dirname(spec_path), ".gitignore")
            if os.path.exists(gitignore):
                with open(gitignore) as f:
                    if GITIGNORE_ENTRY.strip() in f.read():
                        return
                with open(gitignore, "a") as f:
                    f.write("\\n" + GITIGNORE_ENTRY)
            # If no .gitignore exists at the spec level, skip — don't create one unexpectedly


        def _repo_from_git_url(url):
            \"\"\"Extract 'owner/repo' from a GitHub remote URL (SSH or HTTPS).\"\"\"
            m = re.search(r"github\\.com[:/]([^/]+/[^/\\s]+?)(\\.git)?$", url.strip())
            return m.group(1) if m else None


        def _slugify(text, max_len=40):
            return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len]


        def _repo_entry(cfg, target_repo):
            \"\"\"
            Return (path, setup_cmd) for a configured [repos] entry, or (None, None).

            Supports both the simple form:
                [repos]
                "owner/repo" = "/path/to/repo"
            and the richer per-repo table form:
                [repos."owner/repo"]
                path = "/path/to/repo"
                setup_cmd = "npm install"
            \"\"\"
            entry = (cfg.get("repos") or {}).get(target_repo)
            if entry is None:
                return None, None
            if isinstance(entry, str):
                return entry, None
            if isinstance(entry, dict):
                return entry.get("path"), entry.get("setup_cmd")
            return None, None


        def _resolve_work_dir(cfg, target_repo):
            \"\"\"
            Return the local working directory for a job.

            - target_repo is None  → use cwd (card has no GitHub link)
            - found in [repos]     → use that explicit path
            - found via repo_roots → use auto-discovered path
            - set but not found    → return None (caller posts an error to the app)
            \"\"\"
            if not target_repo:
                return os.getcwd()

            path, _setup_cmd = _repo_entry(cfg, target_repo)
            if path:
                return os.path.expanduser(path)

            for root in (cfg.get("repo_roots") or []):
                root = os.path.expanduser(root)
                if not os.path.isdir(root):
                    continue
                try:
                    entries = os.listdir(root)
                except OSError:
                    continue
                for entry in entries:
                    candidate = os.path.join(root, entry)
                    git_cfg = os.path.join(candidate, ".git", "config")
                    if not os.path.isfile(git_cfg):
                        continue
                    try:
                        with open(git_cfg) as f:
                            for line in f:
                                m = re.search(r"url\\s*=\\s*(.+)", line.strip())
                                if m and _repo_from_git_url(m.group(1)) == target_repo:
                                    return candidate
                    except OSError:
                        continue

            return None


        def _make_prompt(branch):
            return (
                f"Please implement the feature described in {SPEC_FILENAME} "
                f"(already written to your working directory). "
                f"You are working on branch {branch} — commit your changes locally as you go. "
                f"Do NOT push to the remote repository; the developer will review and push."
            )


        def _detect_primary_branch(work_dir):
            \"\"\"
            Return 'main', 'master', or similar — the primary branch of the repo.
            Checked against the remote-tracking ref (origin/<name>), not a local
            branch, since worktrees are created directly off origin/<primary>
            without ever checking out the primary branch locally.
            \"\"\"
            r = subprocess.run(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                cwd=work_dir, capture_output=True, text=True,
            )
            if r.returncode == 0:
                return r.stdout.strip().split("/")[-1]
            for name in ("main", "master"):
                r2 = subprocess.run(["git", "rev-parse", "--verify", f"origin/{name}"],
                                    cwd=work_dir, capture_output=True)
                if r2.returncode == 0:
                    return name
            return None


        def _create_worktree(cfg, job, work_dir):
            \"\"\"
            Prepare an isolated git worktree for this job, off a freshly fetched
            primary branch. work_dir (the base clone) is never checked out or
            modified — this works even if you have uncommitted changes sitting
            there, and lets multiple jobs use the same base clone without
            colliding.

            1. git fetch origin (touches no local branch or working tree)
            2. Detect the primary branch from the remote-tracking ref
            3. git worktree add <path> -b qtask/<card_id>-<slug> origin/<primary>
            4. Disable remote push for the session (shared repo config)
            5. Register branch + agent name with the app
            Returns (worktree_path, branch_name, push_url_info) or None on
            failure (error already posted).
            \"\"\"
            job_id  = job["id"]
            card_id = job["card_id"]
            title   = job.get("card_title", "")

            # 1. Fetch — safe regardless of what's checked out or modified in work_dir
            print("[bridge] Fetching latest from origin...")
            r = subprocess.run(["git", "fetch", "origin"],
                               cwd=work_dir, capture_output=True, text=True)
            if r.returncode != 0:
                msg = f"git fetch origin failed — is this a git repo? ({r.stderr.strip()})"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return None

            # 2. Detect primary branch
            primary = _detect_primary_branch(work_dir)
            if not primary:
                msg = "Could not determine primary branch (expected origin/main or origin/master)"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return None

            # 3. Create worktree off origin/<primary>
            slug = _slugify(title)
            branch = f"qtask/{card_id}-{slug}" if slug else f"qtask/{card_id}"

            r = subprocess.run(["git", "rev-parse", "--verify", branch],
                               cwd=work_dir, capture_output=True)
            if r.returncode == 0:
                msg = f"Branch '{branch}' already exists — delete it or push it before rerunning"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return None

            repo_slug = _slugify(os.path.basename(os.path.normpath(work_dir)) or "repo")
            worktree_path = os.path.join(WORKTREES_ROOT, repo_slug, branch.replace("/", "-"))
            os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

            print(f"[bridge] Creating worktree at {worktree_path} (branch {branch})...")
            r = subprocess.run(
                ["git", "worktree", "add", worktree_path, "-b", branch, f"origin/{primary}"],
                cwd=work_dir, capture_output=True, text=True,
            )
            if r.returncode != 0:
                msg = f"git worktree add failed: {r.stderr.strip()}"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return None
            print(f"[bridge] Created branch: {branch}")

            # 4. Disable remote push (safety — Claude Code must not push). This is a
            # shared repo-level config, not per-worktree, so only one job should be
            # active per base repo at a time (true today: jobs run sequentially).
            r = subprocess.run(["git", "config", "remote.origin.pushurl"],
                               cwd=work_dir, capture_output=True, text=True)
            had_push_url = r.returncode == 0
            orig_push_url = r.stdout.strip() if had_push_url else None
            subprocess.run(["git", "config", "remote.origin.pushurl", "no_push"], cwd=work_dir)
            print("[bridge] Remote push disabled for this session.")

            # 5. Register branch + agent with the app
            agent = cfg.get("name") or socket.gethostname().split(".")[0]
            api(cfg, "POST", f"/api/bridge/jobs/{job_id}/start",
                {"branch": branch, "agent": agent})
            print(f"[bridge] Agent: {agent}")

            return worktree_path, branch, (had_push_url, orig_push_url)


        def _git_teardown(work_dir, push_url_info):
            \"\"\"Restore the remote push URL after the session ends (shared repo config).\"\"\"
            had_push_url, orig_push_url = push_url_info
            if had_push_url:
                subprocess.run(["git", "config", "remote.origin.pushurl", orig_push_url],
                               cwd=work_dir)
            else:
                subprocess.run(["git", "config", "--unset", "remote.origin.pushurl"],
                               cwd=work_dir)


        def _run_setup_cmd(worktree_path, setup_cmd):
            \"\"\"Run a one-time dependency-install command in a freshly created
            worktree (e.g. npm install). Non-fatal on failure — Claude Code still
            gets a chance to run; a warning is printed so the user notices.\"\"\"
            if not setup_cmd:
                return
            print(f"[bridge] Running setup_cmd in worktree: {setup_cmd}")
            r = subprocess.run(setup_cmd, cwd=worktree_path, shell=True)
            if r.returncode != 0:
                print(f"[bridge] WARNING: setup_cmd exited with code {r.returncode} — continuing anyway",
                      file=sys.stderr)


        def _run_interactive(cfg, job_id, branch, cwd, prompt_note=True):
            \"\"\"Launch Claude Code as an interactive session the user can engage with.\"\"\"
            print(f"[bridge] Launching Claude Code interactively...")
            print("[bridge] You can interact with Claude in the session below.")
            print("[bridge] When done, type 'exit' or press Ctrl-D.\\n")
            try:
                subprocess.run(["claude", _make_prompt(branch)], cwd=cwd, check=False)
            except FileNotFoundError:
                print("[bridge] ERROR: 'claude' not found.", file=sys.stderr)
                print("[bridge]   npm install -g @anthropic-ai/claude-code", file=sys.stderr)
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
                    {"result": "claude not found on PATH"})
                return False

            print("\\n[bridge] Session ended.")
            result_text = ""
            if prompt_note:
                try:
                    result_text = input("[bridge] Enter a note to save with this job (or press Enter to skip): ").strip()
                except (EOFError, KeyboardInterrupt):
                    pass
            api(cfg, "POST", f"/api/bridge/jobs/{job_id}/complete", {"result": result_text})
            return True


        def _run_streaming(cfg, job_id, branch, cwd):
            \"\"\"Launch Claude Code non-interactively and stream stdout back to the app.\"\"\"
            print(f"[bridge] Launching Claude Code (streaming mode)...")
            try:
                proc = subprocess.Popen(
                    ["claude", "--print", "--dangerously-skip-permissions", _make_prompt(branch)],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError:
                print("[bridge] ERROR: 'claude' not found.", file=sys.stderr)
                print("[bridge]   npm install -g @anthropic-ai/claude-code", file=sys.stderr)
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
                    {"result": "claude not found on PATH"})
                return False

            buffer = []
            last_flush = time.time()

            def flush():
                nonlocal buffer, last_flush
                if not buffer:
                    return
                chunk = "\\n".join(buffer) + "\\n"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/output", {"output": chunk})
                buffer.clear()
                last_flush = time.time()

            for line in proc.stdout:
                line = line.rstrip("\\n")
                print(line)
                buffer.append(line)
                if len(buffer) >= OUTPUT_FLUSH_LINES or (time.time() - last_flush) >= OUTPUT_FLUSH_INTERVAL:
                    flush()

            proc.wait()
            flush()  # final flush

            print(f"\\n[bridge] Claude Code finished (exit {proc.returncode})")
            if proc.returncode == 0:
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/complete", {"result": ""})
            else:
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
                    {"result": f"claude exited with code {proc.returncode}"})
            return True


        def run_job(cfg, job, streaming=False, prompt_note=True):
            job_id      = job["id"]
            card_id     = job["card_id"]
            prompt      = job.get("prompt", "")
            target_repo = job.get("target_repo")

            print(f"\\n[bridge] Job {job_id} — card #{card_id}")

            # Resolve the base repo clone from claude.toml; fall back to cwd for unlinked cards
            work_dir = _resolve_work_dir(cfg, target_repo)
            if work_dir is None:
                msg = (
                    f"No local path configured for '{target_repo}'. "
                    f"Add it to {TOML_FILE} under [repos] or repo_roots."
                )
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return
            if not os.path.isdir(work_dir):
                msg = f"Configured path for '{target_repo}' does not exist: {work_dir}"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return
            if target_repo:
                print(f"[bridge] Repo: {target_repo} → {work_dir}")

            # Isolated worktree off a freshly fetched primary branch — work_dir itself
            # is never touched, so this doesn't require a clean working tree there.
            result = _create_worktree(cfg, job, work_dir)
            if result is None:
                return  # error already posted to the app
            worktree_path, branch, push_url_info = result

            _, setup_cmd = _repo_entry(cfg, target_repo) if target_repo else (None, None)
            setup_cmd = setup_cmd or cfg.get("setup_cmd")
            _run_setup_cmd(worktree_path, setup_cmd)

            print(f"[bridge] Writing {SPEC_FILENAME}...")
            spec_path = os.path.join(worktree_path, SPEC_FILENAME)
            with open(spec_path, "w") as f:
                f.write(prompt)
            ensure_gitignore(spec_path)

            try:
                if streaming:
                    _run_streaming(cfg, job_id, branch, worktree_path)
                else:
                    _run_interactive(cfg, job_id, branch, worktree_path, prompt_note=prompt_note)
            finally:
                _git_teardown(work_dir, push_url_info)
                try:
                    os.remove(spec_path)
                except OSError:
                    pass

            print(f"[bridge] Job {job_id} done. Worktree left at {worktree_path} for review.\\n")


        def cmd_card(cfg, card_id):
            print(f"[bridge] Queueing job for card #{card_id}...")
            job_wrap = api(cfg, "POST", "/api/bridge/jobs", {"card_id": card_id})
            if not job_wrap:
                print("[bridge] Failed to queue job.", file=sys.stderr)
                sys.exit(1)
            # Mark it running and get the prompt
            resp = api(cfg, "GET", "/api/bridge/jobs/next/pending")
            job = resp.get("job") if resp else None
            if not job:
                print("[bridge] Could not claim job (another agent may have picked it up).", file=sys.stderr)
                sys.exit(1)
            run_job(cfg, job, streaming=False)


        def cmd_tag(cfg, tag_name):
            \"\"\"Queue every pending-spec card with this tag, then run them one at a
            time, unattended, each in its own git worktree.\"\"\"
            print(f"[bridge] Queueing jobs tagged '{tag_name}'...")
            resp = api(cfg, "POST", "/api/bridge/jobs/queue-by-tag", {"tag": tag_name})
            if resp is None:
                print("[bridge] Failed to queue jobs.", file=sys.stderr)
                sys.exit(1)

            queued = resp.get("queued") or []
            skipped_no_spec = resp.get("skipped_no_spec") or []
            skipped_already_queued = resp.get("skipped_already_queued") or []

            print(f"[bridge] Queued {len(queued)} job(s) for tag '{tag_name}'.")
            if skipped_no_spec:
                titles = ", ".join(c["title"] for c in skipped_no_spec)
                print(f"[bridge] Skipped {len(skipped_no_spec)} card(s) with no spec yet: {titles}")
            if skipped_already_queued:
                titles = ", ".join(c["title"] for c in skipped_already_queued)
                print(f"[bridge] Skipped {len(skipped_already_queued)} card(s) already queued: {titles}")

            if not queued:
                return

            print(f"\\n[bridge] Running {len(queued)} job(s) sequentially, unattended...\\n")
            for i in range(len(queued)):
                resp2 = api(cfg, "GET", "/api/bridge/jobs/next/pending")
                job = resp2.get("job") if resp2 else None
                if not job:
                    remaining = len(queued) - i
                    print(f"[bridge] No more claimable jobs (expected {remaining} more) — stopping.",
                          file=sys.stderr)
                    break
                print(f"[bridge] ({i + 1}/{len(queued)}) Job {job['id']} — card #{job['card_id']}")
                run_job(cfg, job, streaming=True, prompt_note=False)

            print("[bridge] Tag run complete.")


        def cmd_watch(cfg):
            known_repos = list((cfg.get("repos") or {}).keys())
            repos_qs = ("?repos=" + urllib.parse.quote(",".join(known_repos))) if known_repos else ""
            print(f"[bridge] Watching for jobs (polling every {POLL_INTERVAL}s)... Ctrl-C to stop.\\n")
            if known_repos:
                print(f"[bridge] Filtering to repos: {', '.join(known_repos)}\\n")
            while True:
                try:
                    resp = api(cfg, "GET", f"/api/bridge/jobs/next/pending{repos_qs}")
                    job = resp.get("job") if resp else None
                    if job:
                        run_job(cfg, job, streaming=False, prompt_note=False)
                    else:
                        print(f"[bridge] No pending jobs — sleeping {POLL_INTERVAL}s...", end="\\r")
                        time.sleep(POLL_INTERVAL)
                except KeyboardInterrupt:
                    print("\\n[bridge] Stopped.")
                    break
                except Exception as e:
                    print(f"\\n[bridge] Error: {e}", file=sys.stderr)
                    time.sleep(POLL_INTERVAL)


        def _parse_worktree_porcelain(output):
            \"\"\"Parse `git worktree list --porcelain` output into a list of dicts
            with 'worktree', 'head', and 'branch' keys (blocks are blank-line separated).\"\"\"
            entries = []
            current = {}
            for line in output.splitlines():
                if not line.strip():
                    if current:
                        entries.append(current)
                        current = {}
                    continue
                if line.startswith("worktree "):
                    current["worktree"] = line[len("worktree "):]
                elif line.startswith("branch "):
                    current["branch"] = line[len("branch "):]
                elif line.startswith("HEAD "):
                    current["head"] = line[len("HEAD "):]
            if current:
                entries.append(current)
            return entries


        def _is_branch_merged(work_dir, branch):
            primary = _detect_primary_branch(work_dir)
            if not primary:
                return False
            r = subprocess.run(
                ["git", "merge-base", "--is-ancestor", branch, f"origin/{primary}"],
                cwd=work_dir, capture_output=True,
            )
            return r.returncode == 0


        def cmd_cleanup(cfg):
            \"\"\"List qtask-created worktrees across every repo in [repos], and
            optionally remove some or all of them. Doesn't touch worktrees for
            branches you created yourself (only ones under refs/heads/qtask/).\"\"\"
            repos = cfg.get("repos") or {}
            if not repos:
                print("[bridge] No repos configured in claude.toml [repos] — nothing to scan.")
                return

            found = []  # (repo_name, work_dir, worktree_path, branch)
            for repo_name in repos:
                path, _setup_cmd = _repo_entry(cfg, repo_name)
                work_dir = os.path.expanduser(path) if path else None
                if not work_dir or not os.path.isdir(work_dir):
                    continue
                r = subprocess.run(["git", "worktree", "list", "--porcelain"],
                                   cwd=work_dir, capture_output=True, text=True)
                if r.returncode != 0:
                    continue
                for entry in _parse_worktree_porcelain(r.stdout):
                    branch = entry.get("branch", "")
                    if branch.startswith("refs/heads/qtask/"):
                        found.append((repo_name, work_dir, entry["worktree"],
                                     branch[len("refs/heads/"):]))

            if not found:
                print("[bridge] No qtask worktrees found.")
                return

            print(f"[bridge] Found {len(found)} qtask worktree(s):\\n")
            for i, (repo_name, work_dir, wt_path, branch) in enumerate(found, 1):
                status = "merged" if _is_branch_merged(work_dir, branch) else "not merged"
                print(f"  {i}. [{repo_name}] {branch}  ({status})")
                print(f"     {wt_path}")

            print()
            try:
                choice = input(
                    "Remove which? (comma-separated numbers, 'merged' for all merged, "
                    "Enter to skip): "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not choice:
                return

            if choice.lower() == "merged":
                targets = [f for f in found if _is_branch_merged(f[1], f[3])]
            else:
                indices = {int(x) for x in choice.split(",") if x.strip().isdigit()}
                targets = [f for i, f in enumerate(found, 1) if i in indices]

            for repo_name, work_dir, wt_path, branch in targets:
                print(f"[bridge] Removing worktree {wt_path}...")
                r = subprocess.run(["git", "worktree", "remove", "--force", wt_path],
                                   cwd=work_dir, capture_output=True, text=True)
                if r.returncode != 0:
                    print(f"[bridge] WARNING: could not remove {wt_path}: {r.stderr.strip()}",
                          file=sys.stderr)


        def main():
            parser = argparse.ArgumentParser(description="qtask-bridge: Claude Code bridge agent")
            group = parser.add_mutually_exclusive_group(required=True)
            group.add_argument("--card", type=int, metavar="ID",
                               help="Queue and run job for a specific card")
            group.add_argument("--watch", action="store_true",
                               help="Poll for pending jobs and handle them automatically")
            group.add_argument("--tag", metavar="NAME",
                               help="Queue and run every pending-spec card with this tag, "
                                    "sequentially and unattended, each in its own git worktree")
            group.add_argument("--cleanup", action="store_true",
                               help="List qtask worktrees across configured repos and optionally remove them")
            args = parser.parse_args()

            cfg = load_config()
            if args.watch:
                cmd_watch(cfg)
            elif args.tag:
                cmd_tag(cfg, args.tag)
            elif args.cleanup:
                cmd_cleanup(cfg)
            else:
                cmd_card(cfg, args.card)


        main()
    """)
    return PlainTextResponse(script, media_type="text/plain")
