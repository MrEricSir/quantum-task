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
import textwrap
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

import models
from deps import get_db, AUTH_PASSWORD

router = APIRouter()

_APP_URL = os.getenv("ALLOWED_ORIGIN", "http://localhost:8000")


# ── Schemas ───────────────────────────────────────────────────────────────────

class _JobCreate(BaseModel):
    card_id: int


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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/api/bridge/jobs")
def create_job(body: _JobCreate, db: Session = Depends(get_db)):
    """Queue a bridge job for a card. Card must have a spec."""
    card = db.query(models.Card).filter_by(id=body.card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    if not card.spec:
        raise HTTPException(status_code=400, detail="Card has no spec — generate one first")

    eng_item = None
    if card.external_id:
        eng_item = (
            db.query(models.EngineeringItem)
            .options(selectinload(models.EngineeringItem.comments))
            .filter_by(external_id=card.external_id)
            .first()
        )

    prompt = _build_prompt(card, eng_item)
    now = datetime.now(timezone.utc)
    job = models.BridgeJob(
        card_id=body.card_id,
        status="pending",
        target_repo=_repo_from_external_id(card.external_id),
        spec_snapshot=card.spec,
        prompt_snapshot=prompt,
        created_at=now,
    )
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


@router.get("/api/bridge/install.py", response_class=PlainTextResponse)
def get_install_script():
    """
    Serve a pre-authed install script for qtask-bridge.
    The auth token and app URL are baked in so the user just runs:
        curl https://your-app/api/bridge/install.py | python3
    """
    token = AUTH_PASSWORD or ""
    app_url = _APP_URL.rstrip("/")

    script = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        \"\"\"
        qtask-bridge installer
        Installs the qtask-bridge CLI with your app URL and token pre-configured.
        \"\"\"
        import os, sys, stat, urllib.request, json

        APP_URL = "{app_url}"
        TOKEN   = "{token}"
        INSTALL_DIR = os.path.expanduser("~/.local/bin")
        CONFIG_DIR  = os.path.expanduser("~/.config/qtask-bridge")
        CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
        TOML_FILE   = os.path.join(CONFIG_DIR, "claude.toml")
        BRIDGE_PATH = os.path.join(INSTALL_DIR, "qtask-bridge")

        TOML_TEMPLATE = \"\"\"\\
# qtask-bridge configuration

# Friendly name shown in notifications and the job status panel.
# Defaults to your hostname if left empty.
name = ""

# Map repo slugs to local checkout paths.
# When a job is queued for a card linked to a GitHub issue, the bridge uses
# these mappings to find the right directory automatically.
#
# [repos]
# "owner/myapp" = "/Users/you/code/myapp"
# "owner/api"   = "/Users/you/code/api"

# Alternatively, list root directories and the bridge will discover repos by
# scanning for matching .git remotes automatically.
#
# repo_roots = ["~/code", "~/work"]
\"\"\"

        def main():
            # Download the bridge script from the app
            print("Downloading qtask-bridge...")
            req = urllib.request.Request(
                f"{{APP_URL}}/api/bridge/agent.py",
                headers={{"Authorization": f"Bearer {{TOKEN}}"}},
            )
            with urllib.request.urlopen(req) as r:
                script_content = r.read()

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

            repos = cfg.get("repos") or {}
            if target_repo in repos:
                return os.path.expanduser(repos[target_repo])

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
            \"\"\"Return 'main', 'master', or similar — the primary branch of the repo.\"\"\"
            r = subprocess.run(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                cwd=work_dir, capture_output=True, text=True,
            )
            if r.returncode == 0:
                return r.stdout.strip().split("/")[-1]
            for name in ("main", "master"):
                r2 = subprocess.run(["git", "rev-parse", "--verify", name],
                                    cwd=work_dir, capture_output=True)
                if r2.returncode == 0:
                    return name
            return None


        def _git_setup(cfg, job, work_dir):
            \"\"\"
            Prepare the repo before launching Claude Code:
            1. Abort if there are uncommitted changes.
            2. Detect the primary branch, check it out, and pull.
            3. Create a new local branch qtask/<card_id>-<slug>.
            4. Disable remote push for the duration of the session.
            5. Register branch + agent name with the app.
            Returns (branch_name, push_url_info) or None on failure (error already posted).
            \"\"\"
            job_id  = job["id"]
            card_id = job["card_id"]
            title   = job.get("card_title", "")

            # 1. Uncommitted changes check
            r = subprocess.run(["git", "status", "--porcelain"],
                               cwd=work_dir, capture_output=True, text=True)
            if r.returncode != 0:
                msg = f"git status failed — is this a git repo? ({r.stderr.strip()})"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return None
            if r.stdout.strip():
                msg = "Uncommitted changes detected — commit or stash before running the bridge"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                print(r.stdout.rstrip(), file=sys.stderr)
                return None

            # 2. Detect primary branch, checkout, pull
            primary = _detect_primary_branch(work_dir)
            if not primary:
                msg = "Could not determine primary branch (expected main or master)"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return None

            print(f"[bridge] Switching to {primary} and pulling latest...")
            for git_cmd in (["git", "checkout", primary], ["git", "pull"]):
                r = subprocess.run(git_cmd, cwd=work_dir, capture_output=True, text=True)
                if r.returncode != 0:
                    msg = f"'{' '.join(git_cmd)}' failed: {r.stderr.strip()}"
                    api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                    print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                    return None

            # 3. Create local branch
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
            branch = f"qtask/{card_id}-{slug}" if slug else f"qtask/{card_id}"

            r = subprocess.run(["git", "rev-parse", "--verify", branch],
                               cwd=work_dir, capture_output=True)
            if r.returncode == 0:
                msg = f"Branch '{branch}' already exists — delete it or push it before rerunning"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return None

            r = subprocess.run(["git", "checkout", "-b", branch],
                               cwd=work_dir, capture_output=True, text=True)
            if r.returncode != 0:
                msg = f"git checkout -b {branch} failed: {r.stderr.strip()}"
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
                print(f"\\n[bridge] ERROR: {msg}", file=sys.stderr)
                return None
            print(f"[bridge] Created branch: {branch}")

            # 4. Disable remote push (safety — Claude Code must not push)
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

            return branch, (had_push_url, orig_push_url)


        def _git_teardown(work_dir, push_url_info):
            \"\"\"Restore the remote push URL after the session ends.\"\"\"
            had_push_url, orig_push_url = push_url_info
            if had_push_url:
                subprocess.run(["git", "config", "remote.origin.pushurl", orig_push_url],
                               cwd=work_dir)
            else:
                subprocess.run(["git", "config", "--unset", "remote.origin.pushurl"],
                               cwd=work_dir)


        def _run_interactive(cfg, job_id, branch, prompt_note=True):
            \"\"\"Launch Claude Code as an interactive session the user can engage with.\"\"\"
            print(f"[bridge] Launching Claude Code interactively...")
            print("[bridge] You can interact with Claude in the session below.")
            print("[bridge] When done, type 'exit' or press Ctrl-D.\\n")
            try:
                subprocess.run(["claude", _make_prompt(branch)], check=False)
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


        def _run_streaming(cfg, job_id, branch):
            \"\"\"Launch Claude Code non-interactively and stream stdout back to the app.\"\"\"
            print(f"[bridge] Launching Claude Code (streaming mode)...")
            try:
                proc = subprocess.Popen(
                    ["claude", "--print", "--dangerously-skip-permissions", _make_prompt(branch)],
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

            # Resolve working directory from claude.toml; fall back to cwd for unlinked cards
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

            # Git safety: check clean, checkout primary, create branch, disable push
            result = _git_setup(cfg, job, work_dir)
            if result is None:
                return  # error already posted to the app
            branch, push_url_info = result

            print(f"[bridge] Writing {SPEC_FILENAME}...")
            spec_path = os.path.join(work_dir, SPEC_FILENAME)
            with open(spec_path, "w") as f:
                f.write(prompt)
            ensure_gitignore(spec_path)

            try:
                if streaming:
                    _run_streaming(cfg, job_id, branch)
                else:
                    _run_interactive(cfg, job_id, branch, prompt_note=prompt_note)
            finally:
                _git_teardown(work_dir, push_url_info)
                try:
                    os.remove(spec_path)
                except OSError:
                    pass

            print(f"[bridge] Job {job_id} done.\\n")


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


        def main():
            parser = argparse.ArgumentParser(description="qtask-bridge: Claude Code bridge agent")
            group = parser.add_mutually_exclusive_group(required=True)
            group.add_argument("--card", type=int, metavar="ID",
                               help="Queue and run job for a specific card")
            group.add_argument("--watch", action="store_true",
                               help="Poll for pending jobs and handle them automatically")
            args = parser.parse_args()

            cfg = load_config()
            if args.watch:
                cmd_watch(cfg)
            else:
                cmd_card(cfg, args.card)


        main()
    """)
    return PlainTextResponse(script, media_type="text/plain")
