#!/usr/bin/env python3
"""
qtask-bridge — coding-agent bridge for the todo app.

Usage:
  qtask-bridge --card <id>   Fetch job for a card and launch the coding agent
  qtask-bridge --watch       Poll for pending jobs and handle them automatically
  qtask-bridge --tag <name>  Queue + run every pending-spec card with this tag,
                              sequentially and unattended, each in its own git worktree
  qtask-bridge --list        List qtask worktrees across configured repos (read-only)
  qtask-bridge --cleanup     List and optionally remove finished qtask worktrees

Config files in ~/.config/qtask-bridge/:
  config.json  — app URL and auth token (written by installer)
  claude.toml  — repo mappings and agent name (edit to configure multi-repo)

── Coding-agent adapter contract ───────────────────────────────────────────────
This file is agent-agnostic — it never mentions a specific coding CLI by name.
It expects the following five names to exist in this same module's namespace
at call time, supplied by whichever adapter file (e.g. agent_claude.py) is
concatenated alongside it when the served agent.py is rendered:

    AGENT_LABEL: str                          human-readable name, e.g. "Claude Code"
    AGENT_NOT_FOUND_HINT: str                  install hint shown if the binary is missing
    interactive_command(prompt) -> list[str]   argv to launch an interactive session
    streaming_command(prompt) -> list[str]     argv to launch a non-interactive session
    write_ide_settings(worktree_path) -> None  write any agent-specific IDE config

There's deliberately no `import` between this file and its adapter: the served
artifact must stay a single flat file the installer copies verbatim to
~/.local/bin/qtask-bridge, with no companion files on the target machine. At
serve time (see backend/bridge/render.py) the adapter and core sources are
textually concatenated into one module — definition order doesn't matter
since these names are only *referenced* inside function bodies below, never
at definition time. To try a different coding agent, write a new adapter file
implementing the same five names and point render.py at it instead.
"""
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
HEARTBEAT_INTERVAL = 300  # seconds between heartbeat pings while the agent process runs
SPEC_FILENAME = "BRIDGE_SPEC.md"
ENV_FILENAME = ".env.qtask"
WORKTREES_ROOT = os.path.expanduser("~/.local/share/qtask-bridge/worktrees")
LAST_WORKTREE_FILE = os.path.expanduser("~/.local/share/qtask-bridge/last-worktree")


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


def _repo_from_git_url(url):
    """Extract 'owner/repo' from a GitHub remote URL (SSH or HTTPS)."""
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(\.git)?$", url.strip())
    return m.group(1) if m else None


def _slugify(text, max_len=40):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len]


def _repo_entry(cfg, target_repo):
    """
    Return (path, setup_cmd) for a configured [repos] entry, or (None, None).

    Supports both the simple form:
        [repos]
        "owner/repo" = "/path/to/repo"
    and the richer per-repo table form:
        [repos."owner/repo"]
        path = "/path/to/repo"
        setup_cmd = "npm install"
    """
    entry = (cfg.get("repos") or {}).get(target_repo)
    if entry is None:
        return None, None
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        return entry.get("path"), entry.get("setup_cmd")
    return None, None


def _resolve_work_dir(cfg, target_repo):
    """
    Return the local working directory for a job.

    - target_repo is None  → use cwd (card has no GitHub link)
    - found in [repos]     → use that explicit path
    - found via repo_roots → use auto-discovered path
    - set but not found    → return None (caller posts an error to the app)
    """
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
                        m = re.search(r"url\s*=\s*(.+)", line.strip())
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
        f"Do NOT push to the remote repository; the developer will review and push. "
        f"A reserved port range and database name are provided in {ENV_FILENAME} "
        f"(also in your working directory) — use them for any local dev servers or "
        f"databases you start instead of framework defaults, so this session can't "
        f"collide with anything else already running on this machine."
    )


def _detect_primary_branch(work_dir):
    """
    Return 'main', 'master', or similar — the primary branch of the repo.
    Checked against the remote-tracking ref (origin/<name>), not a local
    branch, since worktrees are created directly off origin/<primary>
    without ever checking out the primary branch locally.
    """
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
    """
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
    """
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
        print(f"\n[bridge] ERROR: {msg}", file=sys.stderr)
        return None

    # 2. Detect primary branch
    primary = _detect_primary_branch(work_dir)
    if not primary:
        msg = "Could not determine primary branch (expected origin/main or origin/master)"
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
        print(f"\n[bridge] ERROR: {msg}", file=sys.stderr)
        return None

    # 3. Create worktree off origin/<primary>
    slug = _slugify(title)
    branch = f"qtask/{card_id}-{slug}" if slug else f"qtask/{card_id}"

    r = subprocess.run(["git", "rev-parse", "--verify", branch],
                       cwd=work_dir, capture_output=True)
    if r.returncode == 0:
        msg = f"Branch '{branch}' already exists — delete it or push it before rerunning"
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
        print(f"\n[bridge] ERROR: {msg}", file=sys.stderr)
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
        print(f"\n[bridge] ERROR: {msg}", file=sys.stderr)
        return None
    print(f"[bridge] Created branch: {branch}")

    # 4. Disable remote push (safety — the coding agent must not push). This is a
    # shared repo-level config, not per-worktree, so only one job should be
    # active per base repo at a time (true today: jobs run sequentially).
    r = subprocess.run(["git", "config", "remote.origin.pushurl"],
                       cwd=work_dir, capture_output=True, text=True)
    had_push_url = r.returncode == 0
    orig_push_url = r.stdout.strip() if had_push_url else None
    subprocess.run(["git", "config", "remote.origin.pushurl", "no_push"], cwd=work_dir)
    print("[bridge] Remote push disabled for this session.")

    # 5. Register branch + agent + worktree path with the app
    agent = cfg.get("name") or socket.gethostname().split(".")[0]
    api(cfg, "POST", f"/api/bridge/jobs/{job_id}/start",
        {"branch": branch, "agent": agent, "worktree_path": worktree_path})
    print(f"[bridge] Agent: {agent}")

    # Point-in-time pointer to the most recent worktree, for a `cd
    # "$(cat ~/.local/share/qtask-bridge/last-worktree)"` shell alias.
    os.makedirs(os.path.dirname(LAST_WORKTREE_FILE), exist_ok=True)
    with open(LAST_WORKTREE_FILE, "w") as f:
        f.write(worktree_path + "\n")

    return worktree_path, branch, (had_push_url, orig_push_url)


def _git_teardown(work_dir, push_url_info):
    """Restore the remote push URL after the session ends (shared repo config)."""
    had_push_url, orig_push_url = push_url_info
    if had_push_url:
        subprocess.run(["git", "config", "remote.origin.pushurl", orig_push_url],
                       cwd=work_dir)
    else:
        subprocess.run(["git", "config", "--unset", "remote.origin.pushurl"],
                       cwd=work_dir)


def _run_setup_cmd(worktree_path, setup_cmd):
    """Run a one-time dependency-install command in a freshly created
    worktree (e.g. npm install). Non-fatal on failure — the coding agent
    still gets a chance to run; a warning is printed so the user notices."""
    if not setup_cmd:
        return
    print(f"[bridge] Running setup_cmd in worktree: {setup_cmd}")
    r = subprocess.run(setup_cmd, cwd=worktree_path, shell=True)
    if r.returncode != 0:
        print(f"[bridge] WARNING: setup_cmd exited with code {r.returncode} — continuing anyway",
              file=sys.stderr)


def _write_qtask_env(worktree_path, job_id):
    """Write a reserved, collision-free port range and database name
    into the worktree, derived deterministically from the job id -- so
    two jobs (or a job and your own dev instance of the same app) can
    never end up on the same port or pointed at the same database.
    Doesn't know or care what framework the target repo uses; it just
    reserves a namespace and leaves the actual wiring (which service
    gets which port) to whoever -- or whichever agent -- reads it.

    Port range: 20000 + (job_id % 400) * 10, ten ports per job -- wide
    enough for a typical frontend + backend + a spare or two, small
    enough that wrapping after 400 concurrently-uncleaned jobs isn't a
    real concern for how this tool is actually used (sequentially --
    the same assumption the shared push-url toggle already relies on).
    job_id (not card_id) so re-running the same card later still gets
    a fresh range even if the old worktree was never cleaned up."""
    port_base = 20000 + (job_id % 400) * 10
    port_end = port_base + 9
    db_name = f"qtask_job_{job_id}"
    content = (
        "# Written by qtask-bridge -- reserved for this job only, so a dev\n"
        "# server or local database started here can't collide with your\n"
        "# main checkout or another job's worktree. Not enforced by\n"
        "# anything -- just a collision-free namespace. Use these instead\n"
        "# of framework defaults for anything you start locally.\n"
        f"QTASK_JOB_ID={job_id}\n"
        f"QTASK_PORT_BASE={port_base}\n"
        f"QTASK_PORT_RANGE={port_base}-{port_end}\n"
        f"QTASK_DB_NAME={db_name}\n"
    )
    env_path = os.path.join(worktree_path, ENV_FILENAME)
    with open(env_path, "w") as f:
        f.write(content)


def _set_terminal_title(title):
    """Set the terminal tab/window title via an OSC escape sequence, so a
    job's tab is identifiable at a glance across multiple open jobs.
    Interactive-mode only — there's no one watching a tab title during
    an unattended --tag/--watch streaming run."""
    sys.stdout.write(f"\033]0;{title}\007")
    sys.stdout.flush()


def _start_heartbeat(cfg, job_id):
    """Start a background thread pinging the job's heartbeat endpoint every
    HEARTBEAT_INTERVAL seconds while the coding agent process runs, so a
    crashed/hung/sleeping-laptop session can be detected server-side even
    though no output is posted while the agent is thinking (interactive
    mode posts nothing at all until the session ends). Agent-agnostic —
    wraps "launch and wait", not the specific launch command. Returns a
    threading.Event; set it to stop the thread once the process exits."""
    stop_event = threading.Event()

    def _loop():
        while not stop_event.wait(HEARTBEAT_INTERVAL):
            api(cfg, "POST", f"/api/bridge/jobs/{job_id}/heartbeat")

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return stop_event


def _run_interactive(cfg, job_id, branch, cwd, prompt_note=True):
    """Launch the coding agent as an interactive session the user can engage with."""
    print(f"[bridge] Launching {AGENT_LABEL} interactively...")
    print("[bridge] You can interact with the agent in the session below.")
    print("[bridge] When done, type 'exit' or press Ctrl-D.\n")
    _set_terminal_title(branch)
    stop_heartbeat = _start_heartbeat(cfg, job_id)
    try:
        try:
            subprocess.run(interactive_command(_make_prompt(branch)), cwd=cwd, check=False)
        except FileNotFoundError:
            print(f"[bridge] ERROR: '{AGENT_LABEL}' not found.", file=sys.stderr)
            print(f"[bridge]   {AGENT_NOT_FOUND_HINT}", file=sys.stderr)
            api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
                {"result": f"{AGENT_LABEL} not found on PATH"})
            return False
    finally:
        stop_heartbeat.set()

    print("\n[bridge] Session ended.")
    result_text = ""
    if prompt_note:
        try:
            result_text = input("[bridge] Enter a note to save with this job (or press Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            pass
    api(cfg, "POST", f"/api/bridge/jobs/{job_id}/complete", {"result": result_text})
    return True


def _run_streaming(cfg, job_id, branch, cwd):
    """Launch the coding agent non-interactively and stream stdout back to the app."""
    print(f"[bridge] Launching {AGENT_LABEL} (streaming mode)...")
    try:
        proc = subprocess.Popen(
            streaming_command(_make_prompt(branch)),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        print(f"[bridge] ERROR: '{AGENT_LABEL}' not found.", file=sys.stderr)
        print(f"[bridge]   {AGENT_NOT_FOUND_HINT}", file=sys.stderr)
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
            {"result": f"{AGENT_LABEL} not found on PATH"})
        return False

    stop_heartbeat = _start_heartbeat(cfg, job_id)

    buffer = []
    last_flush = time.time()

    def flush():
        nonlocal buffer, last_flush
        if not buffer:
            return
        chunk = "\n".join(buffer) + "\n"
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/output", {"output": chunk})
        buffer.clear()
        last_flush = time.time()

    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            print(line)
            buffer.append(line)
            if len(buffer) >= OUTPUT_FLUSH_LINES or (time.time() - last_flush) >= OUTPUT_FLUSH_INTERVAL:
                flush()

        proc.wait()
        flush()  # final flush
    finally:
        stop_heartbeat.set()

    print(f"\n[bridge] {AGENT_LABEL} finished (exit {proc.returncode})")
    if proc.returncode == 0:
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/complete", {"result": ""})
    else:
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
            {"result": f"{AGENT_LABEL} exited with code {proc.returncode}"})
    return True


def run_job(cfg, job, streaming=False, prompt_note=True):
    job_id      = job["id"]
    card_id     = job["card_id"]
    prompt      = job.get("prompt", "")
    target_repo = job.get("target_repo")

    print(f"\n[bridge] Job {job_id} — card #{card_id}")

    # Resolve the base repo clone from claude.toml; fall back to cwd for unlinked cards
    work_dir = _resolve_work_dir(cfg, target_repo)
    if work_dir is None:
        msg = (
            f"No local path configured for '{target_repo}'. "
            f"Add it to {TOML_FILE} under [repos] or repo_roots."
        )
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
        print(f"\n[bridge] ERROR: {msg}", file=sys.stderr)
        return
    if not os.path.isdir(work_dir):
        msg = f"Configured path for '{target_repo}' does not exist: {work_dir}"
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
        print(f"\n[bridge] ERROR: {msg}", file=sys.stderr)
        return
    if target_repo:
        print(f"[bridge] Repo: {target_repo} → {work_dir}")

    # Isolated worktree off a freshly fetched primary branch — work_dir itself
    # is never touched, so this doesn't require a clean working tree there.
    result = _create_worktree(cfg, job, work_dir)
    if result is None:
        return  # error already posted to the app
    worktree_path, branch, push_url_info = result

    # Written before setup_cmd runs, in case it wants to reference the
    # reserved port range / db name too (e.g. to pre-seed a database).
    _write_qtask_env(worktree_path, job_id)

    _, setup_cmd = _repo_entry(cfg, target_repo) if target_repo else (None, None)
    setup_cmd = setup_cmd or cfg.get("setup_cmd")
    _run_setup_cmd(worktree_path, setup_cmd)

    print(f"[bridge] Writing {SPEC_FILENAME}...")
    spec_path = os.path.join(worktree_path, SPEC_FILENAME)
    with open(spec_path, "w") as f:
        f.write(prompt)

    write_ide_settings(worktree_path)

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

    print(f"[bridge] Job {job_id} done. Worktree left at {worktree_path} for review.\n")


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
    """Queue every pending-spec card with this tag, then run them one at a
    time, unattended, each in its own git worktree."""
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

    print(f"\n[bridge] Running {len(queued)} job(s) sequentially, unattended...\n")
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
    print(f"[bridge] Watching for jobs (polling every {POLL_INTERVAL}s)... Ctrl-C to stop.\n")
    if known_repos:
        print(f"[bridge] Filtering to repos: {', '.join(known_repos)}\n")
    while True:
        try:
            resp = api(cfg, "GET", f"/api/bridge/jobs/next/pending{repos_qs}")
            job = resp.get("job") if resp else None
            if job:
                run_job(cfg, job, streaming=False, prompt_note=False)
            else:
                print(f"[bridge] No pending jobs — sleeping {POLL_INTERVAL}s...", end="\r")
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n[bridge] Stopped.")
            break
        except Exception as e:
            print(f"\n[bridge] Error: {e}", file=sys.stderr)
            time.sleep(POLL_INTERVAL)


def _parse_worktree_porcelain(output):
    """Parse `git worktree list --porcelain` output into a list of dicts
    with 'worktree', 'head', and 'branch' keys (blocks are blank-line separated)."""
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


def _scan_qtask_worktrees(cfg):
    """Return (repo_name, work_dir, worktree_path, branch) for every
    worktree, across every repo in [repos], on a branch under
    refs/heads/qtask/ — never a worktree/branch you created yourself.
    Shared by --list and --cleanup so they can't drift out of sync."""
    found = []
    for repo_name in (cfg.get("repos") or {}):
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
    return found


def cmd_list(cfg):
    """Read-only: print every qtask worktree across configured repos,
    with its merge status, and exit. For '--cleanup without the prompt'
    -- e.g. to answer 'where did job N's code go' from any shell."""
    if not (cfg.get("repos") or {}):
        print("[bridge] No repos configured in claude.toml [repos] — nothing to scan.")
        return

    found = _scan_qtask_worktrees(cfg)
    if not found:
        print("[bridge] No qtask worktrees found.")
        return

    print(f"[bridge] {len(found)} qtask worktree(s):\n")
    for repo_name, work_dir, wt_path, branch in found:
        status = "merged" if _is_branch_merged(work_dir, branch) else "not merged"
        print(f"  [{repo_name}] {branch}  ({status})")
        print(f"    {wt_path}")


def cmd_cleanup(cfg):
    """List qtask-created worktrees across every repo in [repos], and
    optionally remove some or all of them. Doesn't touch worktrees for
    branches you created yourself (only ones under refs/heads/qtask/)."""
    if not (cfg.get("repos") or {}):
        print("[bridge] No repos configured in claude.toml [repos] — nothing to scan.")
        return

    found = _scan_qtask_worktrees(cfg)
    if not found:
        print("[bridge] No qtask worktrees found.")
        return

    print(f"[bridge] Found {len(found)} qtask worktree(s):\n")
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
    parser = argparse.ArgumentParser(description="qtask-bridge: coding-agent bridge")
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
    group.add_argument("--list", action="store_true",
                       help="List qtask worktrees across configured repos (read-only, no prompt)")
    args = parser.parse_args()

    cfg = load_config()
    if args.watch:
        cmd_watch(cfg)
    elif args.tag:
        cmd_tag(cfg, args.tag)
    elif args.cleanup:
        cmd_cleanup(cfg)
    elif args.list:
        cmd_list(cfg)
    else:
        cmd_card(cfg, args.card)


if __name__ == "__main__":
    main()
