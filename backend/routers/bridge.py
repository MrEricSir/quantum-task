"""
Bridge endpoints — job queue for the local todo-bridge agent.

Flow:
  1. Frontend POSTs /api/bridge/jobs to queue a job for a card
  2. todo-bridge polls GET /api/bridge/jobs/next and picks up pending jobs
  3. todo-bridge POSTs /api/bridge/jobs/{id}/complete when the session ends
  4. Frontend polls GET /api/bridge/jobs/{id} for status updates

Install endpoint:
  GET /api/bridge/install.py — serves a pre-authed install script for todo-bridge
"""
import os
import textwrap
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
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


# ── Helpers ───────────────────────────────────────────────────────────────────

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
        "id":         job.id,
        "card_id":    job.card_id,
        "status":     job.status,
        "result":     job.result,
        "output":     job.output,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
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
def get_next_pending(db: Session = Depends(get_db)):
    """Bridge polls this to pick up the next pending job."""
    job = (
        db.query(models.BridgeJob)
        .filter_by(status="pending")
        .order_by(models.BridgeJob.created_at)
        .first()
    )
    if not job:
        return {"job": None}

    # Lazily build prompt if not set (e.g. job queued via Telegram, not the frontend)
    if not job.prompt_snapshot:
        card = db.query(models.Card).filter_by(id=job.card_id).first()
        if card:
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
            "prompt": job.prompt_snapshot,
            "spec":   job.spec_snapshot,
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
    Serve a pre-authed install script for todo-bridge.
    The auth token and app URL are baked in so the user just runs:
        curl https://your-app/api/bridge/install.py | python3
    """
    token = AUTH_PASSWORD or ""
    app_url = _APP_URL.rstrip("/")

    script = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        \"\"\"
        todo-bridge installer
        Installs the todo-bridge CLI with your app URL and token pre-configured.
        \"\"\"
        import os, sys, stat, urllib.request, json

        APP_URL = "{app_url}"
        TOKEN   = "{token}"
        INSTALL_DIR = os.path.expanduser("~/.local/bin")
        CONFIG_DIR  = os.path.expanduser("~/.config/todo-bridge")
        CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
        BRIDGE_PATH = os.path.join(INSTALL_DIR, "todo-bridge")

        def main():
            # Download the bridge script from the app
            print("Downloading todo-bridge...")
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

            print(f"Installed: {{BRIDGE_PATH}}")
            print(f"Config:    {{CONFIG_FILE}}")
            print()
            print("Usage (run from your repo directory):")
            print("  todo-bridge --card <card-id>   # run a specific card's job")
            print("  todo-bridge --watch            # poll for jobs automatically")
            print()
            print("Add ~/.local/bin to your PATH if needed:")
            print('  export PATH="$HOME/.local/bin:$PATH"')

        main()
    """)
    return PlainTextResponse(script, media_type="text/plain")


@router.get("/api/bridge/agent.py", response_class=PlainTextResponse)
def get_agent_script():
    """Serve the todo-bridge agent script (downloaded by the installer)."""
    script = textwrap.dedent("""\
        #!/usr/bin/env python3
        \"\"\"
        todo-bridge — Claude Code bridge agent for the todo app.

        Usage:
          todo-bridge --card <id>   Fetch job for a card and launch Claude Code
          todo-bridge --watch       Poll for pending jobs and handle them automatically

        Config: ~/.config/todo-bridge/config.json
          { "app_url": "https://...", "token": "..." }
        \"\"\"
        import argparse
        import json
        import os
        import subprocess
        import sys
        import threading
        import time
        import urllib.request
        import urllib.error

        CONFIG_FILE = os.path.expanduser("~/.config/todo-bridge/config.json")
        POLL_INTERVAL = 10        # seconds between polls in --watch mode
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
                return json.load(f)


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


        CLAUDE_PROMPT = (
            f"Please implement the feature described in {SPEC_FILENAME} "
            f"(already written to your working directory)."
        )


        def _run_interactive(cfg, job_id, spec_path):
            \"\"\"Launch Claude Code as an interactive session the user can engage with.\"\"\"
            print(f"[bridge] Launching Claude Code interactively...")
            print("[bridge] You can interact with Claude in the session below.")
            print("[bridge] When done, type 'exit' or press Ctrl-D.\\n")
            try:
                subprocess.run(["claude", CLAUDE_PROMPT], check=False)
            except FileNotFoundError:
                print("[bridge] ERROR: 'claude' not found.", file=sys.stderr)
                print("[bridge]   npm install -g @anthropic-ai/claude-code", file=sys.stderr)
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
                    {"result": "claude not found on PATH"})
                return False

            print("\\n[bridge] Session ended.")
            result_text = ""
            try:
                result_text = input("[bridge] Enter PR link or summary to save (or press Enter to skip): ").strip()
            except (EOFError, KeyboardInterrupt):
                pass
            api(cfg, "POST", f"/api/bridge/jobs/{job_id}/complete", {"result": result_text})
            return True


        def _run_streaming(cfg, job_id, spec_path):
            \"\"\"Launch Claude Code non-interactively and stream stdout back to the app.\"\"\"
            print(f"[bridge] Launching Claude Code (streaming mode)...")
            try:
                proc = subprocess.Popen(
                    ["claude", "--print", CLAUDE_PROMPT],
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

            if proc.returncode == 0:
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/complete", {"result": ""})
            else:
                api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
                    {"result": f"claude exited with code {proc.returncode}"})
            return True


        def run_job(cfg, job, streaming=False):
            job_id  = job["id"]
            card_id = job["card_id"]
            prompt  = job.get("prompt", "")

            print(f"\\n[bridge] Job {job_id} — card #{card_id}")
            print(f"[bridge] Writing {SPEC_FILENAME}...")

            spec_path = os.path.join(os.getcwd(), SPEC_FILENAME)
            with open(spec_path, "w") as f:
                f.write(prompt)
            ensure_gitignore(spec_path)

            if streaming:
                _run_streaming(cfg, job_id, spec_path)
            else:
                _run_interactive(cfg, job_id, spec_path)

            print(f"[bridge] Job {job_id} marked done.\\n")

            try:
                os.remove(spec_path)
            except OSError:
                pass


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
            print(f"[bridge] Watching for jobs (polling every {POLL_INTERVAL}s, streaming mode)... Ctrl-C to stop.\\n")
            while True:
                try:
                    resp = api(cfg, "GET", "/api/bridge/jobs/next/pending")
                    job = resp.get("job") if resp else None
                    if job:
                        run_job(cfg, job, streaming=True)
                    else:
                        time.sleep(POLL_INTERVAL)
                except KeyboardInterrupt:
                    print("\\n[bridge] Stopped.")
                    break


        def main():
            parser = argparse.ArgumentParser(description="todo-bridge: Claude Code bridge agent")
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
