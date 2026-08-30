#!/usr/bin/env python3
"""
qtask-bridge — coding-agent bridge for the todo app.

Usage:
  qtask-bridge --card <id>   Fetch job for a card and launch the coding agent
  qtask-bridge --watch       Poll for pending jobs and handle them automatically
  qtask-bridge --tag <name>  Queue + run every pending-spec card with this tag,
                              sequentially and unattended, each in its own git worktree
  qtask-bridge --list        List qtask worktrees across configured repos (read-only)
  qtask-bridge --switch      Menu of qtask worktrees for the current repo; prints the
                              chosen path on stdout for a shell function to cd into
  qtask-bridge --cleanup     List and optionally remove finished qtask worktrees
  qtask-bridge --adopt       Detach a qtask worktree from its branch and check the branch
                              out in the primary checkout instead -- reversible via a
                              later --fix/--resume on the same job
  qtask-bridge --run [NAME]  Run the app in a resolved qtask worktree (cwd, last one,
                              or a branch fragment) via its Procfile.dev/Procfile or
                              configured run_cmd
  qtask-bridge --review [NAME]  Read-only lead-engineer-style review of a resolved
                              qtask worktree's changes
  qtask-bridge --unlock-push Clear a stuck no_push sentinel on the current repo's
                              remote.origin.pushurl, left behind by an interrupted job
  qtask-bridge --lock-push   Manually set the no_push sentinel on the current repo,
                              the same safety a job's session gets automatically
  qtask-bridge --rename-branch NEW_NAME  Rename the git branch for a resolved qtask
                              worktree (cwd or last one) and update the app's record

  --agent NAME  Modifier, combinable with any command above: use this coding agent for
                just this run, overriding config.toml's "agent" key for one invocation
                only, e.g. `qtask-bridge --card 84 --agent aider`.

Config files in ~/.config/qtask-bridge/:
  config.json  — app URL and auth token (written by installer)
  config.toml  — repo mappings and agent name (edit to configure multi-repo, or to
                  switch coding agents via the "agent" key, e.g. agent = "claude")

── Coding-agent adapter contract ───────────────────────────────────────────────
This file is agent-agnostic — it never mentions a specific coding CLI by name.
The served agent.py concatenates this file with EVERY known adapter (see
backend/bridge/render.py's _ADAPTER_FILES), not just one, so more than one coding
agent's support can live in a single served script and be chosen at runtime rather
than at render time. Each adapter (e.g. agent_claude.py) defines the same six names,
each suffixed `__{agent}` so they can all coexist in one module namespace without
colliding:

    AGENT_LABEL__{agent}: str                            e.g. "Claude Code"
    AGENT_NOT_FOUND_HINT__{agent}: str                    install hint if the binary is missing
    IDE_SETTINGS_GITIGNORE_ENTRY__{agent}: str | None     path write_ide_settings__{agent}
                                                           writes, or None if it writes nothing
    interactive_command__{agent}(prompt) -> list[str]     argv to launch an interactive session
    streaming_command__{agent}(prompt) -> list[str]       argv to launch a non-interactive session
    write_ide_settings__{agent}(worktree_path) -> None    write any agent-specific IDE config

_activate_adapter() (below) reads config.toml's "agent" key (default "claude") at the
top of main() and aliases the bare names (AGENT_LABEL, interactive_command, etc.) —
which is what every other function in this file actually calls — to whichever
suffix was selected. Everything below this point that touches AGENT_LABEL,
interactive_command, streaming_command, write_ide_settings, or AGENT_NOT_FOUND_HINT
is referring to those bare, runtime-aliased names, not any one adapter's directly.
IDE_SETTINGS_GITIGNORE_ENTRY is the one exception: _activate_adapter() aliases it too
(so the "every adapter defines all six" completeness check stays uniform), but nothing
in this file's CLI logic ever reads the bare name -- its real consumer is install.py's
BRIDGE_IGNORE_ENTRIES, a hand-synced list checked against every adapter's value by
tests/test_bridge_scripts.py rather than derived automatically at render time (weighed
and rejected server-side dynamic derivation as more complexity than justified for one
real adapter; see BRIDGE_MULTI_AGENT_SUPPORT.md's Phase 2 writeup).

There's deliberately no `import` between this file and any adapter: the served
artifact must stay a single flat file the installer copies verbatim to
~/.local/bin/qtask-bridge, with no companion files on the target machine. At
serve time (see backend/bridge/render.py) every source file is textually
concatenated into one module — THIS file goes first (its shebang must be the
served script's literal first line, or the installed, chmod +x'd binary has no
interpreter directive to execute with). Definition order otherwise doesn't matter
for the suffixed names, since _activate_adapter() only *reads* them via
globals() at call time (inside main(), after every concatenated file has already
finished executing), never at definition time -- EXCEPT for the
`if __name__ == "__main__": main()` entrypoint trigger, which executes
immediately at module-exec time. That guard is deliberately NOT included in this
file (see the comment at the bottom, after main()) -- render.py appends it once,
after every file is concatenated, so it can never fire before every adapter's
definitions have actually run. Getting this wrong once already shipped a real
`NameError: name 'write_ide_settings' is not defined` to a live machine. To add a
different coding agent, write a new adapter file implementing the same six names
(suffixed with the new agent's name), add it to render.py's _ADAPTER_FILES, and add its
IDE_SETTINGS_GITIGNORE_ENTRY value (if not None) to install.py's BRIDGE_IGNORE_ENTRIES.
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
import webbrowser
from collections import namedtuple
try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

CONFIG_DIR  = os.path.expanduser("~/.config/qtask-bridge")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TOML_FILE   = os.path.join(CONFIG_DIR, "config.toml")
POLL_INTERVAL = 30        # seconds between polls in --watch mode
OUTPUT_FLUSH_INTERVAL = 5 # seconds between output POSTs while streaming
OUTPUT_FLUSH_LINES = 20   # flush after this many lines even if interval not reached
HEARTBEAT_INTERVAL = 300  # seconds between heartbeat pings while the agent process runs
VERIFICATION_OUTPUT_MAX_LINES = 60  # tail of test_cmd output kept in the verification summary
SPEC_FILENAME = "BRIDGE_SPEC.md"
ENV_FILENAME = ".env.qtask"
# Dropped into every worktree _create_worktree makes, empty content -- proof of provenance
# for _scan_qtask_worktrees to key off instead of the branch name, so a Phase-1 custom branch
# name (which may not start with "qtask/") doesn't make a worktree invisible to
# --list/--switch/--cleanup. See CLAUDE_CODE_INTEGRATION.md's "Phase 2" plan writeup.
WORKTREE_MARKER_FILENAME = ".qtask-worktree"
WORKTREES_ROOT = os.path.expanduser("~/.local/share/qtask-bridge/worktrees")
LAST_WORKTREE_FILE = os.path.expanduser("~/.local/share/qtask-bridge/last-worktree")
PUSH_DISABLED_SENTINEL = "no_push"  # remote.origin.pushurl value while a job holds the base repo
PROCFILE_NAMES = ("Procfile.dev", "Procfile")  # checked in this order; dev-specific wins
PROCESS_COLORS = ("\033[36m", "\033[35m", "\033[33m", "\033[32m", "\033[34m", "\033[31m")  # cycled by index
COLOR_RESET = "\033[0m"


# Top-level config.toml keys that are either copied verbatim into cfg
# (scalars/lists, used directly or as a fallback when a repo entry doesn't
# set its own) or handled specially (repos/repo_roots). Kept as one set so
# _validate_toml_structure can flag anything else as an unrecognized
# top-level key -- almost always a typo.
_TOML_SCALAR_FALLBACK_KEYS = {
    "name", "setup_cmd", "test_cmd", "verify_acceptance", "env_files", "run_cmd", "open_url",
    "agent",
}
_TOML_TOP_LEVEL_KEYS = _TOML_SCALAR_FALLBACK_KEYS | {"repos", "repo_roots"}
_TOML_REPO_TABLE_KEYS = {
    "path", "setup_cmd", "test_cmd", "verify_acceptance", "run_cmd", "env_files", "open_url",
}

# See the "Coding-agent adapter contract" section of this file's module docstring.
# IDE_SETTINGS_GITIGNORE_ENTRY is included so _activate_adapter()'s completeness check (below)
# still requires every adapter to declare it (even as None) -- but nothing at CLI runtime
# actually reads the aliased bare name; its real consumer is install.py's
# BRIDGE_IGNORE_ENTRIES (install-time gitignore setup, hand-synced -- see
# BRIDGE_MULTI_AGENT_SUPPORT.md's Phase 2 for why not derived automatically).
_ADAPTER_CONTRACT_NAMES = (
    "AGENT_LABEL", "AGENT_NOT_FOUND_HINT", "IDE_SETTINGS_GITIGNORE_ENTRY",
    "interactive_command", "streaming_command", "write_ide_settings",
)
_DEFAULT_AGENT = "claude"  # config.toml's "agent" key, when unset


def _activate_adapter(agent_name):
    """Alias the bare adapter-contract names (AGENT_LABEL, interactive_command, etc. -- what
    every other function in this file actually calls) to the `__{agent_name}`-suffixed
    definitions whichever adapter source was concatenated in under. `agent_name` is whatever
    main() resolved -- the `--agent` CLI flag if given, else config.toml's "agent" key, else
    _DEFAULT_AGENT (this function itself doesn't care which source it came from). Called
    once, at the very top of main(), before any subcommand dispatch -- including subcommands
    (--list, --cleanup, --switch, --run, --unlock-push, --lock-push, --rename-branch) that
    never end up touching these names, so a bad agent selection can't block those from working.

    Falls back to _DEFAULT_AGENT with a warning if the configured agent has no adapter
    compiled into this build of the served script (e.g. config.toml says "aider" but this
    qtask-bridge binary predates aider support) -- silently using an unrelated agent would be
    worse than a loud warning plus a known-safe default."""
    g = globals()
    if any(f"{name}__{agent_name}" not in g for name in _ADAPTER_CONTRACT_NAMES):
        print(f"[bridge] Warning: no adapter for coding agent '{agent_name}' in this build "
              f"of qtask-bridge (config.toml's \"agent\" key) -- falling back to "
              f"'{_DEFAULT_AGENT}'. Re-run the installer if you expected {agent_name} support.",
              file=sys.stderr)
        agent_name = _DEFAULT_AGENT
    for name in _ADAPTER_CONTRACT_NAMES:
        g[name] = g[f"{name}__{agent_name}"]


def _validate_toml_structure(toml):
    """Return a list of human-readable problem descriptions for structural
    issues in an already-successfully-parsed config.toml -- wrong types,
    unrecognized/misspelled keys, a repo table missing its required "path".
    None of these are TOML syntax errors (tomllib already succeeded), so
    without this they fail silently: an unrecognized key is just ignored, a
    wrong-type value either gets ignored too or surfaces later as a
    confusing error deep inside whatever command assumed the documented
    shape (e.g. "no run_cmd and no Procfile found" when the user did
    configure a top-level run_cmd fallback, just with a typo'd key)."""
    problems = []

    unknown_top = set(toml.keys()) - _TOML_TOP_LEVEL_KEYS
    if unknown_top:
        problems.append(f"unrecognized top-level key(s): {', '.join(sorted(unknown_top))}")

    for key in ("name", "setup_cmd", "test_cmd", "run_cmd", "open_url", "agent"):
        if key in toml and toml[key] is not None and not isinstance(toml[key], str):
            problems.append(f'"{key}": expected a string, got {type(toml[key]).__name__}')
    if "verify_acceptance" in toml and toml["verify_acceptance"] is not None and not isinstance(toml["verify_acceptance"], bool):
        problems.append(f'"verify_acceptance": expected true/false, got {type(toml["verify_acceptance"]).__name__}')
    if "env_files" in toml and toml["env_files"] is not None and not isinstance(toml["env_files"], list):
        problems.append(f'"env_files": expected a list of paths, got {type(toml["env_files"]).__name__}')

    repos = toml.get("repos")
    if repos is not None and not isinstance(repos, dict):
        problems.append(f'"repos" should be a table, got {type(repos).__name__}')
        repos = {}
    for name, entry in (repos or {}).items():
        if isinstance(entry, str):
            continue  # shorthand "owner/repo" = "/path" form -- nothing to validate
        if not isinstance(entry, dict):
            problems.append(f'repos."{name}": expected a path string or a table, got {type(entry).__name__}')
            continue
        if not entry.get("path"):
            problems.append(f'repos."{name}": missing required "path"')
        unknown = set(entry.keys()) - _TOML_REPO_TABLE_KEYS
        if unknown:
            problems.append(f'repos."{name}": unrecognized key(s): {", ".join(sorted(unknown))}')
        for key in ("path", "setup_cmd", "test_cmd", "run_cmd", "open_url"):
            if key in entry and entry[key] is not None and not isinstance(entry[key], str):
                problems.append(f'repos."{name}".{key}: expected a string, got {type(entry[key]).__name__}')
        if "env_files" in entry and entry["env_files"] is not None and not isinstance(entry["env_files"], list):
            problems.append(f'repos."{name}".env_files: expected a list of paths, got {type(entry["env_files"]).__name__}')
        if "verify_acceptance" in entry and entry["verify_acceptance"] is not None and not isinstance(entry["verify_acceptance"], bool):
            problems.append(f'repos."{name}".verify_acceptance: expected true/false, got {type(entry["verify_acceptance"]).__name__}')

    repo_roots = toml.get("repo_roots")
    if repo_roots is not None:
        if not isinstance(repo_roots, list):
            problems.append(f'"repo_roots" should be a list, got {type(repo_roots).__name__}')
        else:
            for i, r in enumerate(repo_roots):
                if not isinstance(r, str):
                    problems.append(f"repo_roots[{i}]: expected a string, got {type(r).__name__}")

    return problems


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
    cfg.setdefault("agent", _DEFAULT_AGENT)
    if tomllib and os.path.exists(TOML_FILE):
        try:
            with open(TOML_FILE, "rb") as f:
                toml = tomllib.load(f)
        except Exception as e:
            print(f"[bridge] Warning: could not parse {TOML_FILE}: {e}", file=sys.stderr)
            print(f"[bridge] Falling back to {CONFIG_FILE} only -- repos, repo_roots, and any "
                  f"fallback commands configured in config.toml are NOT in effect until this is fixed.",
                  file=sys.stderr)
        else:
            # Every top-level scalar/list fallback config.toml documents
            # ("setup_cmd"/"run_cmd"/etc as a repo-less default) -- not just
            # name/repos/repo_roots. Previously only those three were copied
            # here, so a top-level run_cmd/setup_cmd/etc fallback silently
            # never took effect even though the installed TOML_TEMPLATE
            # explicitly documents setting them this way.
            for key in _TOML_SCALAR_FALLBACK_KEYS:
                if key in toml:
                    cfg[key] = toml[key]
            cfg["repos"]      = dict(toml.get("repos") or {})
            cfg["repo_roots"] = list(toml.get("repo_roots") or [])

            problems = _validate_toml_structure(toml)
            if problems:
                print(f"[bridge] Warning: {len(problems)} problem(s) in {TOML_FILE}:", file=sys.stderr)
                for p in problems:
                    print(f"  - {p}", file=sys.stderr)
                print("[bridge] These entries are ignored/behave as unconfigured until fixed.", file=sys.stderr)
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


RepoEntry = namedtuple(
    "RepoEntry",
    ["path", "setup_cmd", "test_cmd", "verify_acceptance", "run_cmd", "env_files", "open_url"],
)
_EMPTY_REPO_ENTRY = RepoEntry(None, None, None, None, None, None, None)


def _repo_entry(cfg, target_repo):
    """
    Return a RepoEntry namedtuple for a configured [repos] entry, or all-None
    fields if unconfigured. A plain namedtuple (not a dict) so the two
    existing `path, *_ = _repo_entry(...)` call sites keep working
    unchanged -- it's still a tuple, just with named fields for the call
    sites that need more than one value, since positional unpacking of
    seven fields (`_, _, _, _, run_cmd, _, open_url = ...`) is exactly the
    kind of thing that silently breaks when a field gets inserted in the
    middle later.

    Supports both the simple form:
        [repos]
        "owner/repo" = "/path/to/repo"
    and the richer per-repo table form:
        [repos."owner/repo"]
        path = "/path/to/repo"
        setup_cmd = "npm install"
        test_cmd = "npm test"
        verify_acceptance = true
        run_cmd = "npm run dev"
        env_files = ["backend/.env", "frontend/.env"]
        open_url = "http://localhost:$((QTASK_PORT_BASE + 1))"
    """
    entry = (cfg.get("repos") or {}).get(target_repo)
    if entry is None:
        return _EMPTY_REPO_ENTRY
    if isinstance(entry, str):
        return RepoEntry(entry, None, None, None, None, None, None)
    if isinstance(entry, dict):
        return RepoEntry(
            entry.get("path"), entry.get("setup_cmd"),
            entry.get("test_cmd"), entry.get("verify_acceptance"),
            entry.get("run_cmd"), entry.get("env_files"), entry.get("open_url"),
        )
    return _EMPTY_REPO_ENTRY


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

    path, *_ = _repo_entry(cfg, target_repo)
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


def _make_prompt(branch, worktree_path):
    return _make_agent_prompt(
        branch, worktree_path,
        action=(
            f"Please implement the feature described in {SPEC_FILENAME} "
            f"(already written to your working directory)."
        ),
    )


def _make_fix_prompt(branch, worktree_path):
    """Wrapper prompt for a fix job (run_job's resumes_job_id branch) -- deliberately framed
    as "apply these specific fixes," not a general invitation to refactor, per
    CLAUDE_CODE_INTEGRATION.md's "CodeRabbit feedback integration" plan. The actual fix
    content (which comments, file/line, suggested diffs) lives in SPEC_FILENAME, same as a
    normal job's feature spec -- built server-side by bridge.jobs._build_fix_prompt."""
    return _make_agent_prompt(
        branch, worktree_path,
        action=(
            f"Please apply the specific fixes described in {SPEC_FILENAME} "
            f"(already written to your working directory) -- these are targeted review "
            f"comments (file, line, and a suggested change where one was given), not a "
            f"general invitation to refactor. Only address what's listed there."
        ),
    )


def _make_resume_prompt(branch, worktree_path):
    """Wrapper prompt for a resume job (run_job's resumes_job_id branch, no fix_comment_ids) --
    picking up an interrupted session in an existing worktree rather than starting fresh. The
    actual task content lives in SPEC_FILENAME same as any other job -- built server-side by
    bridge.jobs._build_resume_prompt, which already tells the agent to check git log/diff
    before continuing. See CLAUDE_CODE_INTEGRATION.md's "Phase 0" plan."""
    return _make_agent_prompt(
        branch, worktree_path,
        action=(
            f"Please continue the task described in {SPEC_FILENAME} "
            f"(already written to your working directory) -- a previous session on this "
            f"exact branch was interrupted before finishing. Check `git log` and `git diff` "
            f"against the base branch first to see what's already been done, then pick up "
            f"from there instead of starting over."
        ),
    )


def _make_agent_prompt(branch, worktree_path, action):
    """Shared tail (branch/push/env-file/Procfile instructions) between _make_prompt and
    _make_fix_prompt -- only the opening action line differs between "implement a feature"
    and "apply specific fixes."""
    prompt = (
        f"{action} "
        f"You are working on branch {branch} — commit your changes locally as you go. "
        f"Do NOT push to the remote repository; the developer will review and push. "
        f"A reserved port range and database name are provided in {ENV_FILENAME} "
        f"(also in your working directory) — use them for any local dev servers or "
        f"databases you start instead of framework defaults, so this session can't "
        f"collide with anything else already running on this machine."
    )
    # If the repo has a Procfile.dev/Procfile, tell the agent about it directly --
    # otherwise it has to rediscover "this app has a separate frontend/backend,
    # here's how to start each" on its own mid-session. Same file _run_procfile
    # already knows how to read for `qtask-bridge --run`.
    procfile_path = _find_procfile(worktree_path)
    if procfile_path:
        processes = _parse_procfile(procfile_path)
        proc_lines = "\n".join(f"  {name}: {cmd}" for name, cmd in processes.items())
        prompt += (
            f"\n\nThis repo has a {os.path.basename(procfile_path)} defining how to run its "
            f"processes -- use it (with the port range and database name from "
            f"{ENV_FILENAME} above) if you need to start the app to test your changes:\n"
            f"{proc_lines}"
        )
    return prompt


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


def _disable_remote_push(work_dir):
    """Disable remote push for the session on the base repo clone (safety — the coding
    agent must never push, the developer reviews and pushes). Shared repo-level config, not
    per-worktree. Returns push_url_info for _git_teardown to restore afterward.

    Extracted out of _create_worktree so a fix job (which resumes an existing worktree
    instead of creating one, see run_job's resumes_job_id branch) gets the exact same safety
    property without going through worktree creation at all."""
    r = subprocess.run(["git", "config", "remote.origin.pushurl"],
                       cwd=work_dir, capture_output=True, text=True)
    had_push_url = r.returncode == 0
    orig_push_url = r.stdout.strip() if had_push_url else None
    if orig_push_url == PUSH_DISABLED_SENTINEL:
        # A previous run left this stuck -- e.g. it crashed somewhere between here and
        # _git_teardown running, before teardown ever had a chance to restore it (see
        # run_job's try/finally). Without this check, every future run would keep
        # "restoring" pushurl right back to the broken value forever, since it looks like
        # the original. Treat it as if there was never a real pushurl, so teardown unsets
        # it this time instead of perpetuating the bad value.
        had_push_url = False
        orig_push_url = None
        print("[bridge] Found a stale push-disable from an interrupted previous run — clearing it.")
    subprocess.run(["git", "config", "remote.origin.pushurl", PUSH_DISABLED_SENTINEL], cwd=work_dir)
    print("[bridge] Remote push disabled for this session.")
    return (had_push_url, orig_push_url)


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
       (or the user-supplied requested_branch_name verbatim, if the job has one)
    4. Disable remote push for the session (shared repo config)
    5. Register branch + agent name with the app
    Returns (worktree_path, branch_name, push_url_info) or None on
    failure (error already posted).
    """
    job_id  = job["id"]
    card_id = job["card_id"]
    title   = job.get("card_title", "")
    requested_branch = job.get("requested_branch_name")

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

    # 3. Create worktree off origin/<primary> -- the requested name verbatim if the user
    # supplied one at queue time, else the auto-generated qtask/<card_id>-<slug> default.
    if requested_branch:
        branch = requested_branch
    else:
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

    # Marker of provenance -- see WORKTREE_MARKER_FILENAME's own comment above.
    with open(os.path.join(worktree_path, WORKTREE_MARKER_FILENAME), "w"):
        pass
    # Belt-and-suspenders alongside install.py's global gitignore setup (BRIDGE_IGNORE_ENTRIES):
    # that only takes effect once the installer has been (re-)run, so on a machine that
    # created worktrees before this marker existed, the marker would otherwise show up as an
    # untracked, uncommitted file forever -- silently swept into the very first
    # _commit_if_dirty auto-commit any session makes. A LOCAL exclude (shared by every
    # worktree of this repo, since they share .git via --git-common-dir) doesn't depend on
    # install-time setup at all. Idempotent -- append-if-missing, same pattern as
    # setup_global_gitignore's own check.
    exclude_path = os.path.join(work_dir, ".git", "info", "exclude")
    try:
        existing = ""
        if os.path.isfile(exclude_path):
            with open(exclude_path) as f:
                existing = f.read()
        if WORKTREE_MARKER_FILENAME not in existing:
            os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
            needs_leading_newline = existing and not existing.endswith("\n")
            with open(exclude_path, "a") as f:
                if needs_leading_newline:
                    f.write("\n")
                f.write(f"{WORKTREE_MARKER_FILENAME}\n")
    except OSError as e:
        print(f"[bridge] WARNING: could not update {exclude_path}: {e}", file=sys.stderr)

    # 4. Disable remote push (safety — the coding agent must not push).
    push_url_info = _disable_remote_push(work_dir)

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

    return worktree_path, branch, push_url_info


def _commit_if_dirty(worktree_path, job_id, reason):
    """Safety net: if worktree_path has uncommitted changes, commit them with an
    auto-generated message rather than risk losing them. Nothing enforces the prompt's "commit
    as you go" instruction (_make_agent_prompt, above) -- agent_claude.py has no equivalent to
    Aider's --auto-commits -- so an interrupted session (crash, Ctrl-C, network loss) can
    otherwise leave real progress sitting uncommitted and invisible to anything that inspects
    the branch afterward (git log, a resume/fix job, --adopt, a --review diff). Called both at
    the end of every session (the common case: a stray uncommitted edit) and at the start of a
    resume-type session (the crash-recovery case: the PREVIOUS session never got to run its
    own end-of-session cleanup at all). See CLAUDE_CODE_INTEGRATION.md's "Phase 0" plan.

    job_id may be None -- cmd_review's apply-flow (CLAUDE_CODE_INTEGRATION.md's "Phase C")
    shares this same safety net but has no BridgeJob of its own (deliberately: reusing just
    the safety net, not the full job-queue mechanism, so this works even for a worktree whose
    branch can't be traced back to a card at all). The commit message just omits "job #N" in
    that case rather than printing a misleading "job #None".

    Returns True if a commit was made, False if the worktree was already clean or the commit
    itself failed (logged, not raised -- this is best-effort insurance, not something that
    should crash the job over)."""
    r = subprocess.run(["git", "status", "--porcelain"],
                       cwd=worktree_path, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return False
    print(f"[bridge] Uncommitted changes found ({reason}) -- auto-committing to avoid losing them...")
    subprocess.run(["git", "add", "-A"], cwd=worktree_path, capture_output=True, text=True)
    message = f"Auto-captured: uncommitted changes ({reason})"
    if job_id is not None:
        message += f" -- job #{job_id}"
    r = subprocess.run(["git", "commit", "-m", message],
                       cwd=worktree_path, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[bridge] WARNING: auto-commit failed: {r.stderr.strip()}", file=sys.stderr)
        return False
    print("[bridge] Auto-committed.")
    return True


def _reverse_adoption_if_needed(work_dir, worktree_path, branch):
    """If worktree_path was --adopt'ed (its branch is currently checked out in the PRIMARY
    directory instead of the worktree itself), reverse it before resuming: check primary
    back out to the base branch, then re-attach `branch` to the worktree. See
    CLAUDE_CODE_INTEGRATION.md's "Phase 2" plan for why this is reversible rather than a
    one-way move, and cmd_adopt for the operation this undoes.

    Detected via the worktree's own HEAD: --adopt leaves it on a detached HEAD (`git
    checkout --detach`) specifically so the branch is free for primary to check out -- a
    worktree that's still on its own branch has nothing to reverse. No state needs to be
    remembered from adopt time (like what primary was on before): it always returns to the
    same well-defined place, the repo's actual primary branch.

    Returns True if nothing needed reversing or the reversal succeeded, False if the
    reversal itself failed (e.g. primary has uncommitted changes blocking its own checkout)
    -- caller should treat False as fatal for this job, not proceed against a worktree
    that's still missing its branch."""
    r = subprocess.run(["git", "branch", "--show-current"], cwd=worktree_path,
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return True  # still on its own branch -- nothing was adopted

    primary = _detect_primary_branch(work_dir)
    if not primary:
        print("[bridge] ERROR: worktree was adopted into primary, but could not detect the "
              "primary branch to reverse it.", file=sys.stderr)
        return False

    print(f"[bridge] {worktree_path} was adopted into the primary checkout -- reversing "
          f"before resuming (checking primary back to {primary}, re-attaching {branch} to "
          f"the worktree)...")
    r = subprocess.run(["git", "checkout", primary], cwd=work_dir, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[bridge] ERROR: could not check primary back out to {primary}: "
              f"{r.stderr.strip()}", file=sys.stderr)
        return False
    r = subprocess.run(["git", "checkout", branch], cwd=worktree_path, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[bridge] ERROR: could not re-attach {branch} to the worktree: "
              f"{r.stderr.strip()}", file=sys.stderr)
        return False
    print("[bridge] Reversed.")
    return True


def _git_teardown(work_dir, push_url_info):
    """Restore the remote push URL after the session ends (shared repo config)."""
    had_push_url, orig_push_url = push_url_info
    if had_push_url:
        subprocess.run(["git", "config", "remote.origin.pushurl", orig_push_url],
                       cwd=work_dir)
    else:
        subprocess.run(["git", "config", "--unset", "remote.origin.pushurl"],
                       cwd=work_dir)


def cmd_unlock_push(cwd=None):
    """Manually clear a stuck no_push sentinel on the CURRENT repo's
    remote.origin.pushurl. _git_teardown already restores this automatically
    when a job's session ends normally, and _create_worktree's own
    stale-sentinel check (above) clears it the next time a job runs against
    the same repo -- but neither helps in the moment, mid "thorny git
    situation", when you just want to push right now without waiting on a
    new job. Operates on whatever repo the cwd resolves to (git's own
    upward directory search finds the right one), not a specific worktree
    target -- pushurl is shared base-repo config, not per-worktree, so
    there's nothing to disambiguate.

    Only ever touches the exact sentinel value this tool itself sets -- a
    real custom pushurl configured for an unrelated reason is left alone,
    with a message saying so, rather than silently clobbered."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print("[bridge] Not inside a git repo.", file=sys.stderr)
        return
    repo_dir = r.stdout.strip()

    r = subprocess.run(["git", "config", "remote.origin.pushurl"],
                       cwd=repo_dir, capture_output=True, text=True)
    current = r.stdout.strip() if r.returncode == 0 else None

    if current != PUSH_DISABLED_SENTINEL:
        if current:
            print(f"[bridge] remote.origin.pushurl is already set to something else "
                  f"({current!r}) -- leaving it alone, that's your own config, not ours.")
        else:
            print("[bridge] Push isn't locked here -- nothing to do.")
        return

    r = subprocess.run(["git", "config", "--unset", "remote.origin.pushurl"], cwd=repo_dir)
    if r.returncode != 0:
        print(f"[bridge] Could not unset remote.origin.pushurl in {repo_dir}.", file=sys.stderr)
        return
    print(f"[bridge] Push unlocked for {repo_dir}.")


def cmd_lock_push(cwd=None):
    """Manually set the no_push sentinel on the CURRENT repo's
    remote.origin.pushurl -- the same safety property a job's session gets
    automatically (see _disable_remote_push), for when you want it right
    now on a repo you're about to poke around in by hand, without waiting
    on a new job to start one. Mirrors cmd_unlock_push's shape exactly:
    same cwd-resolves-the-repo behavior (pushurl is shared base-repo
    config, not per-worktree, so there's no worktree target to
    disambiguate), same refusal to touch a real custom pushurl that isn't
    our sentinel.

    Only ever sets the exact sentinel value this tool itself checks for --
    a real custom pushurl configured for an unrelated reason is left
    alone, with a message saying so, rather than silently clobbered."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print("[bridge] Not inside a git repo.", file=sys.stderr)
        return
    repo_dir = r.stdout.strip()

    r = subprocess.run(["git", "config", "remote.origin.pushurl"],
                       cwd=repo_dir, capture_output=True, text=True)
    current = r.stdout.strip() if r.returncode == 0 else None

    if current == PUSH_DISABLED_SENTINEL:
        print("[bridge] Push is already locked here -- nothing to do.")
        return
    if current:
        print(f"[bridge] remote.origin.pushurl is already set to something else "
              f"({current!r}) -- leaving it alone, that's your own config, not ours.")
        return

    r = subprocess.run(["git", "config", "remote.origin.pushurl", PUSH_DISABLED_SENTINEL], cwd=repo_dir)
    if r.returncode != 0:
        print(f"[bridge] Could not set remote.origin.pushurl in {repo_dir}.", file=sys.stderr)
        return
    print(f"[bridge] Push locked for {repo_dir}.")


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


def _link_env_files(worktree_path, work_dir, env_files):
    """Symlink each configured env file from the base repo into the fresh
    worktree. `git worktree add` only ever checks out tracked files, and
    .env files are gitignored by definition -- without this, --run (or the
    coding agent itself) has no real secrets/config to work with. Symlinked
    rather than copied: a copy would scatter live secrets across every
    worktree directory and drift the moment the source file changes; a
    symlink stays in sync and there's only ever one real copy on disk.

    Paths in env_files are relative to the repo root and can point anywhere
    in the tree (e.g. "backend/.env", "frontend/.env") -- resolved against
    work_dir on the source side, worktree_path on the destination side,
    same convention setup_cmd/run_cmd already use for their cwd.

    Best-effort and non-fatal per file: a missing source, or a real
    (non-symlink) file already sitting at the destination, is skipped with
    a warning rather than failing the whole job over an optional
    convenience or silently clobbering something unexpected."""
    for rel_path in env_files or []:
        src = os.path.join(work_dir, rel_path)
        dst = os.path.join(worktree_path, rel_path)
        if not os.path.isfile(src):
            print(f"[bridge] WARNING: env_files entry {rel_path!r} not found at {src} — skipping",
                  file=sys.stderr)
            continue
        if os.path.islink(dst):
            os.remove(dst)
        elif os.path.exists(dst):
            print(f"[bridge] WARNING: {dst} already exists and isn't a symlink — "
                  f"leaving it alone instead of overwriting", file=sys.stderr)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.symlink(src, dst)
        print(f"[bridge] Linked {rel_path} from base repo")


def _set_terminal_title(title):
    """Set the terminal tab/window title via an OSC escape sequence, so a
    job's tab is identifiable at a glance across multiple open jobs.
    Interactive-mode only — there's no one watching a tab title during
    an unattended --tag/--watch streaming run."""
    sys.stdout.write(f"\033]0;{title}\007")
    sys.stdout.flush()


def _check_for_requested_rename(cfg, job_id, cwd, heartbeat_response):
    """If the webapp asked (mid-session, via the Code tab) for this job's branch to be
    renamed, the heartbeat response carries it in requested_branch_name -- see
    bridge/router.py's request_job_rename for the full mechanism this is the other half
    of. Renames the currently-checked-out branch (no need to know its old name) and
    confirms back via /rename-branch so the server's own record stays in sync. Asks git
    for the actual current branch fresh every call rather than tracking it in a local
    variable, so this stays correct even across multiple renames in one session."""
    requested = (heartbeat_response or {}).get("requested_branch_name")
    if not requested:
        return
    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=cwd, capture_output=True, text=True,
    ).stdout.strip()
    if not current or current == requested:
        return
    r = subprocess.run(["git", "branch", "-m", requested], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"\n[bridge] WARNING: failed to rename branch to '{requested}': {r.stderr.strip()}")
        return
    print(f"\n[bridge] Branch renamed to '{requested}' (requested from the app)")
    api(cfg, "POST", f"/api/bridge/jobs/{job_id}/rename-branch", {"branch_name": requested})


def _start_heartbeat(cfg, job_id, cwd):
    """Start a background thread pinging the job's heartbeat endpoint every
    HEARTBEAT_INTERVAL seconds while the coding agent process runs, so a
    crashed/hung/sleeping-laptop session can be detected server-side even
    though no output is posted while the agent is thinking (interactive
    mode posts nothing at all until the session ends). Agent-agnostic —
    wraps "launch and wait", not the specific launch command. Also checks each
    heartbeat's response for a mid-session branch-rename request (see
    _check_for_requested_rename) -- `git branch -m` only touches refs, not the working
    tree, so it's safe to run concurrently with the coding agent editing files in the
    same worktree. Returns a threading.Event; set it to stop the thread once the
    process exits."""
    stop_event = threading.Event()

    def _loop():
        while not stop_event.wait(HEARTBEAT_INTERVAL):
            resp = api(cfg, "POST", f"/api/bridge/jobs/{job_id}/heartbeat")
            _check_for_requested_rename(cfg, job_id, cwd, resp)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return stop_event


def _extract_section(spec_text, heading):
    """Return the body of a `## <heading>` markdown section (everything up
    to the next `## ` heading or end of string), or None if not found (a
    hand-written spec may not have one). Not hardcoded to acceptance
    criteria specifically -- reusable for other spec sections a future
    verification check might want."""
    if not spec_text:
        return None
    marker = f"## {heading}"
    start = spec_text.find(marker)
    if start == -1:
        return None
    start += len(marker)
    next_heading = spec_text.find("\n## ", start)
    body = spec_text[start:next_heading] if next_heading != -1 else spec_text[start:]
    return body.strip() or None


def _git_diff_stat(worktree_path):
    """Return `git diff --stat` (file names + line counts, not the actual code) of this
    branch's changes against the primary branch's base ref, or "" if it can't be computed
    (detached primary branch, git error) -- best-effort context for a cross-repo companion
    job, not something that should ever break the completion report. Captured client-side,
    entirely local -- no GitHub PR or push required. See BRIDGE_CROSS_REPO_JOBS.md Phase 4."""
    primary = _detect_primary_branch(worktree_path)
    if not primary:
        return ""
    r = subprocess.run(
        ["git", "diff", "--stat", f"origin/{primary}...HEAD"],
        cwd=worktree_path, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def _run_test_cmd(worktree_path, test_cmd):
    """Run the configured test command in the worktree and summarize the
    result. Purely mechanical -- no LLM call, no judgment about whether a
    failure matters, just pass/fail and a truncated tail of output."""
    print(f"[bridge] Running test_cmd: {test_cmd}")
    r = subprocess.run(test_cmd, cwd=worktree_path, shell=True,
                       capture_output=True, text=True)
    output = (r.stdout or "") + (r.stderr or "")
    lines = output.splitlines()
    if len(lines) > VERIFICATION_OUTPUT_MAX_LINES:
        lines = lines[-VERIFICATION_OUTPUT_MAX_LINES:]
    tail = "\n".join(lines)
    status = "passed" if r.returncode == 0 else f"failed (exit {r.returncode})"
    print(f"[bridge] test_cmd {status}")
    summary = f"### Tests (`{test_cmd}`)\n\n**{status}**"
    if tail:
        summary += f"\n\n```\n{tail}\n```"
    return summary


def _make_acceptance_check_prompt(criteria_text, test_summary=None):
    """Build a read-only prompt checking the diff against acceptance
    criteria. Explicitly forbidden from modifying anything -- verification
    reports, it doesn't fix; fixing is the still-deferred review pass's
    job, and keeping the two separate is what makes this cheap and safe to
    run unattended by default once enabled."""
    parts = [
        "Compare your changes in this worktree (git diff against the primary branch) "
        "against the acceptance criteria below. For each item, report MET or NOT MET "
        "with a one-line reason. Do not modify, create, or delete any files -- this is "
        "a read-only check, not an implementation task.",
        "",
        "## Acceptance Criteria",
        criteria_text,
    ]
    if test_summary:
        parts += ["", "## Test Results", test_summary]
    return "\n".join(parts)


def _check_acceptance_criteria(worktree_path, criteria_text, test_summary=None):
    """Run a focused, non-interactive check of the diff against the
    acceptance criteria. Reuses streaming_command directly -- already
    exactly the non-interactive launch this needs -- rather than adding a
    new adapter contract name for it."""
    prompt = _make_acceptance_check_prompt(criteria_text, test_summary)
    print("[bridge] Checking acceptance criteria...")
    r = subprocess.run(streaming_command(prompt), cwd=worktree_path,
                       capture_output=True, text=True)
    report = (r.stdout or "").strip() or "(no output)"
    return f"### Acceptance Criteria\n\n{report}"


def _run_verification(worktree_path, test_cmd, verify_acceptance, spec_text):
    """Run configured verification checks after the coding session ends,
    before the job is marked complete. Both checks are opt-in (test_cmd
    unset = skip, verify_acceptance defaults False) so existing installs
    behave exactly as before with no config changes. Returns a markdown
    block to prepend to the job's result, or "" if nothing ran."""
    sections = []
    test_summary = None
    if test_cmd:
        test_summary = _run_test_cmd(worktree_path, test_cmd)
        sections.append(test_summary)
    if verify_acceptance:
        criteria = _extract_section(spec_text, "Acceptance Criteria")
        if criteria:
            sections.append(_check_acceptance_criteria(worktree_path, criteria, test_summary))
        else:
            print("[bridge] verify_acceptance is on but no Acceptance Criteria "
                  "section found in spec — skipping")
    if not sections:
        return ""
    return "## Verification\n\n" + "\n\n".join(sections)


def _run_interactive(cfg, job_id, branch, cwd, prompt_note=True,
                      test_cmd=None, verify_acceptance=False, spec_text=None,
                      prompt_kind="normal"):
    """Launch the coding agent as an interactive session the user can engage with.

    prompt_kind selects the wrapper wording: "fix" for _make_fix_prompt (resuming an existing
    worktree to address specific comments), "resume" for _make_resume_prompt (resuming after
    an interrupted session), "normal" for _make_prompt (a fresh feature job) -- see run_job's
    resumes_job_id branch."""
    print(f"[bridge] Launching {AGENT_LABEL} interactively...")
    print("[bridge] You can interact with the agent in the session below.")
    print("[bridge] When done, type 'exit' or press Ctrl-D.\n")
    _set_terminal_title(branch)
    stop_heartbeat = _start_heartbeat(cfg, job_id, cwd)
    verification = ""
    make_prompt = {"fix": _make_fix_prompt, "resume": _make_resume_prompt}.get(
        prompt_kind, _make_prompt)
    try:
        try:
            subprocess.run(interactive_command(make_prompt(branch, cwd)), cwd=cwd, check=False)
        except FileNotFoundError:
            print(f"[bridge] ERROR: '{AGENT_LABEL}' not found.", file=sys.stderr)
            print(f"[bridge]   {AGENT_NOT_FOUND_HINT}", file=sys.stderr)
            api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
                {"result": f"{AGENT_LABEL} not found on PATH"})
            return False
        # Heartbeat stays alive through verification too — a slow but
        # legitimate check shouldn't get mistaken for a stalled job.
        verification = _run_verification(cwd, test_cmd, verify_acceptance, spec_text)
    finally:
        stop_heartbeat.set()
        # In the finally, not just the next line -- a KeyboardInterrupt (or anything else)
        # during the subprocess.run/_run_verification above must not skip this. That's
        # exactly the interruption scenario this safety net exists for in the first place;
        # skipping it here would mean the only thing catching leftover dirty state is the
        # NEXT resume attempt, if there ever is one. Matches _git_teardown's guarantee level.
        _commit_if_dirty(cwd, job_id, "session ended")

    print("\n[bridge] Session ended.")
    result_text = ""
    if prompt_note:
        try:
            result_text = input("[bridge] Enter a note to save with this job (or press Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            pass
    if verification:
        result_text = f"{verification}\n\n{result_text}" if result_text else verification
    api(cfg, "POST", f"/api/bridge/jobs/{job_id}/complete",
        {"result": result_text, "diff_summary": _git_diff_stat(cwd)})
    return True


def _run_streaming(cfg, job_id, branch, cwd,
                    test_cmd=None, verify_acceptance=False, spec_text=None,
                    prompt_kind="normal"):
    """Launch the coding agent non-interactively and stream stdout back to the app.

    prompt_kind selects the wrapper wording -- see _run_interactive's docstring."""
    print(f"[bridge] Launching {AGENT_LABEL} (streaming mode)...")
    make_prompt = {"fix": _make_fix_prompt, "resume": _make_resume_prompt}.get(
        prompt_kind, _make_prompt)
    try:
        proc = subprocess.Popen(
            streaming_command(make_prompt(branch, cwd)),
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

    stop_heartbeat = _start_heartbeat(cfg, job_id, cwd)

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

    verification = ""
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            print(line)
            buffer.append(line)
            if len(buffer) >= OUTPUT_FLUSH_LINES or (time.time() - last_flush) >= OUTPUT_FLUSH_INTERVAL:
                flush()

        proc.wait()
        flush()  # final flush
        # Only verify a session that actually succeeded — nothing useful to
        # test against one that didn't. Heartbeat stays alive through it.
        if proc.returncode == 0:
            verification = _run_verification(cwd, test_cmd, verify_acceptance, spec_text)
    finally:
        stop_heartbeat.set()
        # In the finally, not just the next line -- see _run_interactive's matching comment.
        _commit_if_dirty(cwd, job_id, "session ended")

    print(f"\n[bridge] {AGENT_LABEL} finished (exit {proc.returncode})")
    if proc.returncode == 0:
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/complete",
            {"result": verification, "diff_summary": _git_diff_stat(cwd)})
    else:
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error",
            {"result": f"{AGENT_LABEL} exited with code {proc.returncode}"})
    return True


def run_job(cfg, job, streaming=False, prompt_note=True, suggest_next=True):
    job_id      = job["id"]
    card_id     = job["card_id"]
    prompt      = job.get("prompt", "")
    spec_text   = job.get("spec")
    target_repo = job.get("target_repo")
    resumes_job_id = job.get("resumes_job_id")
    # A resume-type job (resumes_job_id set) is either a targeted "fix" (specific comments
    # to address, fix_comment_ids set by _queue_fix_job) or a general "resume" (continuing
    # after an interrupted session, fix_comment_ids left unset by _queue_resume_job) -- see
    # CLAUDE_CODE_INTEGRATION.md's "Phase 0" plan.
    if resumes_job_id and job.get("fix_comment_ids"):
        prompt_kind = "fix"
    elif resumes_job_id:
        prompt_kind = "resume"
    else:
        prompt_kind = "normal"

    print(f"\n[bridge] Job {job_id} — card #{card_id}")

    # Resolve the base repo clone from config.toml; fall back to cwd for unlinked cards
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

    if resumes_job_id:
        # Resume the worktree/branch this fix targets instead of creating a fresh one --
        # bridge.jobs._queue_fix_job already copied branch_name/worktree_path from the
        # original job onto this one at creation time. See CLAUDE_CODE_INTEGRATION.md's
        # "CodeRabbit feedback integration" plan for why this doesn't try to auto-recreate
        # a worktree/branch that --cleanup already removed -- it errors out instead, loudly,
        # with a message telling the user to resolve it manually.
        worktree_path = job.get("worktree_path")
        branch = job.get("branch_name")
        if not worktree_path or not os.path.isdir(worktree_path):
            msg = (
                f"No resumable worktree found for job {resumes_job_id} "
                f"(expected at {worktree_path or '<unknown>'}). It may already have been "
                f"removed via --cleanup -- resolve manually or re-run the original job."
            )
            api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
            print(f"\n[bridge] ERROR: {msg}", file=sys.stderr)
            return
        # If this worktree was --adopt'ed since it last ran, its branch is currently
        # checked out in primary instead of here -- reverse that before anything else, or
        # everything below would proceed against a worktree still missing its own branch.
        if not _reverse_adoption_if_needed(work_dir, worktree_path, branch):
            msg = (
                f"Could not resume job {resumes_job_id} -- its worktree was adopted into "
                f"the primary checkout and reversing that failed (see bridge output above). "
                f"Resolve manually, then re-run."
            )
            api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": msg})
            print(f"\n[bridge] ERROR: {msg}", file=sys.stderr)
            return
        # Safety net for the case this resume exists to handle in the first place: the
        # ORIGINAL session may have ended uncleanly (crash, Ctrl-C, network loss) and never
        # reached its own end-of-session _commit_if_dirty call at all -- catch it here,
        # before a fresh agent process starts working on top of it.
        _commit_if_dirty(worktree_path, job_id, "resuming a previous session")
        push_url_info = _disable_remote_push(work_dir)
        agent = cfg.get("name") or socket.gethostname().split(".")[0]
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/start",
            {"branch": branch, "agent": agent, "worktree_path": worktree_path})
        print(f"[bridge] Resuming worktree at {worktree_path} (branch {branch})")
    else:
        # Isolated worktree off a freshly fetched primary branch — work_dir itself
        # is never touched, so this doesn't require a clean working tree there.
        result = _create_worktree(cfg, job, work_dir)
        if result is None:
            return  # error already posted to the app
        worktree_path, branch, push_url_info = result

    # Computed either way (a fix job still runs test_cmd/verify_acceptance the same as any
    # other job) -- only the one-time setup steps below (_write_qtask_env/_link_env_files/
    # _run_setup_cmd) are skipped for a fix job, since the worktree they'd prepare already
    # exists from the original job that created it.
    entry = _repo_entry(cfg, target_repo) if target_repo else _EMPTY_REPO_ENTRY
    setup_cmd = entry.setup_cmd or cfg.get("setup_cmd")
    test_cmd = entry.test_cmd or cfg.get("test_cmd")
    verify_acceptance = entry.verify_acceptance
    if verify_acceptance is None:
        verify_acceptance = cfg.get("verify_acceptance", False)
    env_files = entry.env_files or cfg.get("env_files")

    # Everything from here on runs with the base repo's remote push disabled
    # (push_url_info holds what to restore). ALL of it -- not just the
    # coding session itself -- must be inside this try/finally: an exception
    # anywhere in here (setup_cmd, writing BRIDGE_SPEC.md, write_ide_settings)
    # used to be able to skip _git_teardown entirely, leaving
    # remote.origin.pushurl stuck at PUSH_DISABLED_SENTINEL in the user's
    # real repo -- exactly what a real write_ide_settings NameError did on a
    # live machine. Never narrow this back down to just the run_streaming/
    # run_interactive call.
    spec_path = None
    try:
        if not resumes_job_id:
            # Written before setup_cmd runs, in case it wants to reference the
            # reserved port range / db name too (e.g. to pre-seed a database).
            _write_qtask_env(worktree_path, job_id)
            _link_env_files(worktree_path, work_dir, env_files)
            _run_setup_cmd(worktree_path, setup_cmd)

        print(f"[bridge] Writing {SPEC_FILENAME}...")
        spec_path = os.path.join(worktree_path, SPEC_FILENAME)
        with open(spec_path, "w") as f:
            f.write(prompt)

        if not resumes_job_id:
            write_ide_settings(worktree_path)

        if streaming:
            _run_streaming(cfg, job_id, branch, worktree_path,
                            test_cmd=test_cmd, verify_acceptance=verify_acceptance,
                            spec_text=spec_text, prompt_kind=prompt_kind)
        else:
            _run_interactive(cfg, job_id, branch, worktree_path, prompt_note=prompt_note,
                              test_cmd=test_cmd, verify_acceptance=verify_acceptance,
                              spec_text=spec_text, prompt_kind=prompt_kind)
    except Exception as e:
        # Best-effort: run_streaming/run_interactive already report their
        # own outcome (claude not found, non-zero exit, etc.) via /complete
        # or /error internally. This catches everything BEFORE that point
        # (setup_cmd, spec writing, write_ide_settings) so the job doesn't
        # just sit at "running" forever with no explanation -- it'll still
        # get caught by the 20-minute stale-job sweep either way, but this
        # is immediate and gives a real reason.
        api(cfg, "POST", f"/api/bridge/jobs/{job_id}/error", {"result": f"Bridge setup failed: {e}"})
        raise
    finally:
        _git_teardown(work_dir, push_url_info)
        if spec_path:
            try:
                os.remove(spec_path)
            except OSError:
                pass

    print(f"[bridge] Job {job_id} done. Worktree left at {worktree_path} for review.")
    if suggest_next:
        # Skipped for a --tag batch (cmd_tag passes suggest_next=False) -- unattended runs
        # would otherwise print this once per job in a row, which is exactly the case where
        # nobody's watching in real time for it to be useful; cmd_tag prints one summary hint
        # after the whole batch instead.
        print("[bridge] Next: qtask-bridge --review to check the code, or --run to try it, before you push.\n")
    else:
        print()


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
        run_job(cfg, job, streaming=True, prompt_note=False, suggest_next=False)

    print("[bridge] Tag run complete.")
    print("[bridge] Next: qtask-bridge --list to see the resulting worktrees, "
          "--review each branch before pushing.")


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
    """Return (repo_name, work_dir, worktree_path, branch) for every worktree, across every
    repo in [repos], that qtask-bridge itself created -- never a worktree/branch you created
    yourself. Shared by --list and --cleanup so they can't drift out of sync.

    Detected via WORKTREE_MARKER_FILENAME's presence in the worktree, not the branch name --
    Phase 1's branch-name override (a user can name their branch anything, not just
    qtask/<id>-<slug>) would otherwise make an overridden worktree invisible here, since the
    old check was purely "does the branch start with qtask/". Also still matches on the old
    refs/heads/qtask/ prefix as a fallback, for worktrees created before this marker file
    existed -- an older worktree without the marker shouldn't suddenly become untrackable."""
    found = []
    for repo_name in (cfg.get("repos") or {}):
        path, *_ = _repo_entry(cfg, repo_name)
        work_dir = os.path.expanduser(path) if path else None
        if not work_dir or not os.path.isdir(work_dir):
            continue
        r = subprocess.run(["git", "worktree", "list", "--porcelain"],
                           cwd=work_dir, capture_output=True, text=True)
        if r.returncode != 0:
            continue
        for entry in _parse_worktree_porcelain(r.stdout):
            branch = entry.get("branch", "")
            has_marker = os.path.isfile(os.path.join(entry["worktree"], WORKTREE_MARKER_FILENAME))
            if has_marker or branch.startswith("refs/heads/qtask/"):
                found.append((repo_name, work_dir, entry["worktree"],
                             branch[len("refs/heads/"):] if branch else "(detached)"))
    return found


def _current_repo_name(cfg):
    """Return the [repos] entry name that the CURRENT directory belongs to,
    whether cwd is the repo's main checkout or one of its qtask worktrees --
    both share the same underlying .git, found via --git-common-dir, so this
    works no matter which one you're standing in. Returns None if cwd isn't
    inside any configured repo."""
    def common_dir(path):
        r = subprocess.run(["git", "-C", path, "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return os.path.abspath(os.path.join(path, r.stdout.strip()))

    cwd_common = common_dir(os.getcwd())
    if not cwd_common:
        return None

    for repo_name in (cfg.get("repos") or {}):
        path, *_ = _repo_entry(cfg, repo_name)
        work_dir = os.path.expanduser(path) if path else None
        if not work_dir or not os.path.isdir(work_dir):
            continue
        if common_dir(work_dir) == cwd_common:
            return repo_name
    return None


def _branch_last_active(work_dir, branch):
    """Unix timestamp of branch's most recent commit -- used to sort --switch's
    menu by actual work recency rather than worktree directory creation time,
    so a worktree that's still being actively committed to stays near the top
    even if it's one of the older ones on disk. Returns 0 (sorts last) if git
    fails for any reason."""
    r = subprocess.run(["git", "log", "-1", "--format=%ct", branch],
                       cwd=work_dir, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return 0
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def _format_age(timestamp):
    if not timestamp:
        return "unknown"
    delta = max(0, time.time() - timestamp)
    if delta < 3600:
        return f"{max(1, int(delta // 60))}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def cmd_switch(cfg):
    """Print a numbered menu of qtask worktrees for the CURRENT repo, most
    recently active first, and prompt for a selection -- then print ONLY the
    chosen worktree path on stdout. Everything else (the menu, the prompt,
    every error message) goes to stderr, so a shell function can safely do
    `cd "$(qtask-bridge --switch)"` without the menu text leaking into the
    captured path. Replaces the old single-target `qcd` tip (which could only
    jump to the single last worktree) with an actual picker across every
    worktree for the repo you're currently in."""
    repo_name = _current_repo_name(cfg)
    if repo_name is None:
        print("[bridge] Not inside a configured repo (check config.toml [repos]).",
              file=sys.stderr)
        return

    found = [f for f in _scan_qtask_worktrees(cfg) if f[0] == repo_name]
    if not found:
        print(f"[bridge] No qtask worktrees found for '{repo_name}'.", file=sys.stderr)
        return

    found.sort(key=lambda f: _branch_last_active(f[1], f[3]), reverse=True)
    cwd = os.path.abspath(os.getcwd())

    print(f"[bridge] qtask worktrees for '{repo_name}' (most recent first):\n",
          file=sys.stderr)
    for i, (_, work_dir, wt_path, branch) in enumerate(found, 1):
        status = "merged" if _is_branch_merged(work_dir, branch) else "not merged"
        age = _format_age(_branch_last_active(work_dir, branch))
        marker = " (current)" if os.path.abspath(wt_path) == cwd else ""
        print(f"  {i}. {branch}  --  {age}, {status}{marker}", file=sys.stderr)

    sys.stderr.write("\nSwitch to which? (number, Enter to cancel): ")
    sys.stderr.flush()
    try:
        choice = input().strip()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return
    if not choice:
        return
    if not choice.isdigit() or not (1 <= int(choice) <= len(found)):
        print("[bridge] Invalid selection.", file=sys.stderr)
        return

    print(found[int(choice) - 1][2])  # stdout: the chosen path, and nothing else


def cmd_list(cfg):
    """Read-only: print every qtask worktree across configured repos,
    with its merge status, and exit. For '--cleanup without the prompt'
    -- e.g. to answer 'where did job N's code go' from any shell."""
    if not (cfg.get("repos") or {}):
        print("[bridge] No repos configured in config.toml [repos] — nothing to scan.")
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
    optionally remove some or all of them -- worktree AND branch. Doesn't
    touch worktrees you created yourself -- see _scan_qtask_worktrees for how
    "qtask-created" is actually detected."""
    if not (cfg.get("repos") or {}):
        print("[bridge] No repos configured in config.toml [repos] — nothing to scan.")
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
            print(f"[bridge] WARNING: could not remove worktree {wt_path}: {r.stderr.strip()}",
                  file=sys.stderr)

        # Delete the branch too -- leaving it behind is exactly what causes
        # "Branch '<branch>' already exists" on the next run for the same
        # card, even after the worktree itself is gone (_create_worktree
        # checks whether the branch exists before it ever touches
        # worktrees, so a leftover branch alone is enough to block a retry).
        print(f"[bridge] Deleting branch {branch}...")
        r = subprocess.run(["git", "branch", "-D", branch],
                           cwd=work_dir, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[bridge] WARNING: could not delete branch {branch}: {r.stderr.strip()}",
                  file=sys.stderr)


def cmd_adopt(cfg):
    """Detach a qtask worktree from its branch and check the branch out in the primary
    checkout instead, so you can keep working on it directly there -- reversible, not a
    one-way move: a later --fix/--resume on the job that made this worktree automatically
    reverses it (checks primary back to the base branch, re-attaches the branch to the
    worktree) before resuming, see run_job's resumes_job_id branch and
    _reverse_adoption_if_needed. See CLAUDE_CODE_INTEGRATION.md's "Phase 2" plan for the
    full reasoning, including why this isn't `git worktree remove` (that would permanently
    foreclose --fix/--resume on the job, which depends on the worktree still existing)."""
    if not (cfg.get("repos") or {}):
        print("[bridge] No repos configured in config.toml [repos] — nothing to scan.")
        return

    found = _scan_qtask_worktrees(cfg)
    if not found:
        print("[bridge] No qtask worktrees found.")
        return

    print(f"[bridge] Found {len(found)} qtask worktree(s):\n")
    for i, (repo_name, work_dir, wt_path, branch) in enumerate(found, 1):
        print(f"  {i}. [{repo_name}] {branch}")
        print(f"     {wt_path}")

    print()
    try:
        choice = input("Adopt which one into its primary checkout? (number, Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not choice:
        return
    if not choice.isdigit() or not (1 <= int(choice) <= len(found)):
        print("[bridge] Invalid selection.", file=sys.stderr)
        return

    repo_name, work_dir, wt_path, branch = found[int(choice) - 1]

    # Refuse if the job currently using this worktree is still actively running -- pulling
    # it out from under a live agent session would corrupt it. Keyed by worktree_path (not
    # branch name/card id) since a Phase 1 custom branch name isn't reliably parseable back
    # to a card id -- see the by-worktree endpoint's own docstring.
    resp = api(cfg, "GET", f"/api/bridge/jobs/by-worktree?path={urllib.parse.quote(wt_path)}")
    latest = resp.get("job") if resp else None
    if latest and latest["status"] in ("pending", "running"):
        print(f"[bridge] Job {latest['id']} for this worktree is still {latest['status']} -- "
              f"wait for it to finish (or stall/error out) before adopting it.", file=sys.stderr)
        return

    r = subprocess.run(["git", "status", "--porcelain"], cwd=wt_path, capture_output=True, text=True)
    if r.stdout.strip():
        print(f"[bridge] {wt_path} has uncommitted changes -- commit or stash them before "
              f"adopting.", file=sys.stderr)
        return

    r = subprocess.run(["git", "status", "--porcelain"], cwd=work_dir, capture_output=True, text=True)
    if r.stdout.strip():
        print(f"[bridge] {work_dir} (primary checkout) has uncommitted changes -- commit or "
              f"stash them there before adopting.", file=sys.stderr)
        return

    print(f"[bridge] Detaching {wt_path} from {branch}...")
    r = subprocess.run(["git", "checkout", "--detach"], cwd=wt_path, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[bridge] ERROR: could not detach worktree: {r.stderr.strip()}", file=sys.stderr)
        return

    print(f"[bridge] Checking out {branch} in {work_dir}...")
    r = subprocess.run(["git", "checkout", branch], cwd=work_dir, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[bridge] ERROR: could not check out {branch} in the primary checkout: "
              f"{r.stderr.strip()}", file=sys.stderr)
        print(f"[bridge] Re-attaching {wt_path} to {branch} to avoid leaving it stranded...",
              file=sys.stderr)
        subprocess.run(["git", "checkout", branch], cwd=wt_path, capture_output=True)
        return

    print(f"[bridge] Adopted. {branch} is now checked out in {work_dir}.")
    print(f"[bridge] {wt_path} is left in place (detached) -- a later --fix/--resume on this "
          f"job automatically reverses this before resuming.")


def _find_procfile(worktree_path):
    """Return the path to Procfile.dev if present, else Procfile, else None.
    Procfile.dev wins deliberately -- a bare Procfile in a repo root is
    often meant for production/Heroku (expects $PORT, a real database),
    which isn't safe to run as-is against a scratch dev worktree. This
    mirrors the same Procfile.dev-first convention Rails 7+ ships in
    bin/dev, rather than inventing a new file format."""
    for name in PROCFILE_NAMES:
        candidate = os.path.join(worktree_path, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _parse_procfile(path):
    """Parse `name: command` lines into an ordered dict, skipping blank
    lines and #-comments."""
    processes = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, command = line.partition(":")
            if not sep:
                continue
            processes[name.strip()] = command.strip()
    return processes


def _load_env_file(path):
    """Parse the KEY=value format _write_qtask_env writes (skipping blank
    lines and #-comments) into a dict. Returns {} if the file doesn't
    exist -- not every worktree that gets --run has one (e.g. one created
    before this file existed)."""
    if not os.path.isfile(path):
        return {}
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if sep:
                env[key.strip()] = value.strip()
    return env


def _resolve_open_url(worktree_path, extra_env, open_url_template):
    """Resolve an open_url template (e.g. "http://localhost:$((QTASK_PORT_BASE
    + 1))") against the worktree's reserved-port env vars via a real shell --
    the same "let a shell evaluate it" approach _run_procfile/
    _run_single_command already use for arbitrary run commands, so port
    arithmetic like the "+1" above just works without a bespoke templating
    engine of our own. QTASK_PORT_BASE alone isn't reliably "the" dev server
    port to guess at automatically -- this repo's own Procfile.dev, for
    example, puts the backend on QTASK_PORT_BASE and the frontend on
    QTASK_PORT_BASE+1 -- so this has to stay an explicit per-repo setting,
    not an automatic default. Returns None (and warns) if the template
    doesn't evaluate cleanly, rather than opening a browser tab to a
    obviously-wrong or empty URL."""
    if not open_url_template:
        return None
    env = {**os.environ, **extra_env}
    r = subprocess.run(["sh", "-c", f'printf %s "{open_url_template}"'],
                       cwd=worktree_path, env=env, capture_output=True, text=True)
    url = r.stdout.strip()
    if r.returncode != 0 or not url:
        print(f"[bridge] WARNING: could not resolve open_url {open_url_template!r} — skipping",
              file=sys.stderr)
        return None
    return url


def _open_when_ready(url, timeout=10):
    """Poll the URL until it responds (or timeout), then open it in the
    default browser. Opening immediately, before the dev server has
    actually bound its port, would often land on a connection-refused
    error page -- startup time varies a lot (a near-instant Vite frontend
    vs. a Python backend with slow imports). Opens anyway once the timeout
    elapses even if it never responded, rather than silently doing nothing
    -- a slow-loading tab you can refresh is a better failure mode than no
    tab at all. Runs in its own thread (see caller) so it never blocks
    _run_procfile/_run_single_command's own output relay."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    webbrowser.open(url)
    print(f"[bridge] Opened {url}")


def _run_procfile(worktree_path, procfile_path, extra_env, stop_event=None):
    """Run every process in a Procfile concurrently, relay their output
    with a colorized [name] prefix, and stop all of them together --
    either on Ctrl-C, when any one of them exits on its own (matching
    Foreman/Honcho's default: Procfile processes are normally
    interdependent, so partial survival isn't useful), or when
    stop_event is set (a testability hook so tests don't have to fake
    OS-level Ctrl-C)."""
    processes = _parse_procfile(procfile_path)
    env = {**os.environ, **extra_env}
    procs = {}
    for name, command in processes.items():
        procs[name] = subprocess.Popen(
            command, shell=True, cwd=worktree_path, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )

    def _relay(name, proc, color):
        prefix = f"{color}[{name}]{COLOR_RESET}"
        for line in proc.stdout:
            line = line.rstrip("\n")
            print(f"{prefix} {line}")

    threads = []
    for i, (name, proc) in enumerate(procs.items()):
        color = PROCESS_COLORS[i % len(PROCESS_COLORS)]
        t = threading.Thread(target=_relay, args=(name, proc, color), daemon=True)
        t.start()
        threads.append(t)

    print(f"[bridge] Running {len(procs)} process(es): {', '.join(procs)} "
          "(Ctrl-C to stop all)")
    exited_name = None
    try:
        while exited_name is None:
            if stop_event is not None and stop_event.is_set():
                break
            for name, proc in procs.items():
                if proc.poll() is not None:
                    exited_name = name
                    break
            else:
                time.sleep(0.3)
                continue
    except KeyboardInterrupt:
        print("\n[bridge] Stopping...")

    if exited_name is not None:
        print(f"[bridge] '{exited_name}' exited (code {procs[exited_name].returncode}) — "
              "stopping the rest.")

    for name, proc in procs.items():
        if proc.poll() is None:
            proc.terminate()
    for name, proc in procs.items():
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    print("[bridge] All processes stopped.")


def _run_single_command(worktree_path, run_cmd, extra_env):
    """Run a single run_cmd in the foreground. No special signal handling
    needed -- one process in the same terminal foreground group as this
    script, so Ctrl-C reaches it naturally."""
    env = {**os.environ, **extra_env}
    print(f"[bridge] Running: {run_cmd}")
    subprocess.run(run_cmd, shell=True, cwd=worktree_path, env=env)


def _prompt_pick_one(found, prompt_text="Multiple matches — pick one (number): "):
    """Print a numbered list and prompt for a single selection. Returns
    the chosen (repo_name, work_dir, worktree_path, branch) tuple, or
    None if the prompt was skipped/invalid. Same numbered-list style as
    --cleanup's picker, but single-select only -- --cleanup's own picker
    also supports comma-separated multi-select and a 'merged' bulk
    keyword, which don't apply to picking one worktree to run."""
    for i, (repo_name, _, _, branch) in enumerate(found, 1):
        print(f"  {i}. [{repo_name}] {branch}")
    try:
        choice = input(prompt_text).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not choice.isdigit():
        return None
    index = int(choice)
    if not (1 <= index <= len(found)):
        return None
    return found[index - 1]


def _resolve_worktree_target(cfg, target):
    """Resolve which qtask worktree `--run` should act on. Reuses
    _scan_qtask_worktrees for every step (not just fragment matching) so
    --run can never disagree with --list/--cleanup about what counts as
    a qtask worktree:

    1. No target given: is an ancestor of cwd one of the known worktree
       paths? (covers the common flow -- qcd or any other discoverability
       mechanism got you there, now you want to try it)
    2. No target, not in a worktree: fall back to the last-worktree file,
       matched back against the scan so repo_name/branch travel with it.
    3. Target given: case-insensitive substring match against branch
       names. Exactly one match wins; multiple prompts a single-select
       picker; zero prints available branches and returns None.

    Returns (repo_name, work_dir, worktree_path, branch) or None (caller
    already has enough context to print why).
    """
    found = _scan_qtask_worktrees(cfg)
    if not found:
        print("[bridge] No qtask worktrees found. Run a job first, or check "
              "config.toml [repos].")
        return None

    if not target:
        cwd = os.path.abspath(os.getcwd())
        wt_paths = {os.path.abspath(f[2]): f for f in found}
        ancestor = cwd
        while True:
            if ancestor in wt_paths:
                return wt_paths[ancestor]
            parent = os.path.dirname(ancestor)
            if parent == ancestor:
                break
            ancestor = parent

        if os.path.isfile(LAST_WORKTREE_FILE):
            with open(LAST_WORKTREE_FILE) as f:
                last_path = f.read().strip()
            match = wt_paths.get(os.path.abspath(last_path))
            if match:
                return match

        print("[bridge] Not inside a qtask worktree and no last-worktree on record. "
              "Pass a branch fragment: qtask-bridge --run <branch>")
        return None

    matches = [f for f in found if target.lower() in f[3].lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        picked = _prompt_pick_one(matches)
        if picked is None:
            print("[bridge] No selection made.")
        return picked

    print(f"[bridge] No qtask worktree matches '{target}'. Available branches:")
    for repo_name, _, _, branch in found:
        print(f"  [{repo_name}] {branch}")
    return None


def cmd_run(cfg, target):
    """Run the app in a resolved qtask worktree: a Procfile.dev/Procfile
    if the worktree has one (multi-process, e.g. separate frontend/backend
    dev servers), else a configured run_cmd (single process), else a
    helpful message naming both options. .env.qtask's reserved port/DB
    vars are auto-injected into whatever runs, same idea as
    foreman/honcho auto-loading .env -- no manual `source .env.qtask`
    step needed. If open_url is configured, opens it in the default
    browser once the dev server actually responds (see
    _resolve_open_url/_open_when_ready) -- covers both branches below
    since either one might be what actually serves the webapp."""
    resolved = _resolve_worktree_target(cfg, target)
    if resolved is None:
        return
    repo_name, work_dir, worktree_path, branch = resolved
    print(f"[bridge] [{repo_name}] {branch}\n[bridge] {worktree_path}\n")

    extra_env = _load_env_file(os.path.join(worktree_path, ENV_FILENAME))
    entry = _repo_entry(cfg, repo_name)

    open_url = _resolve_open_url(worktree_path, extra_env, entry.open_url or cfg.get("open_url"))
    if open_url:
        threading.Thread(target=_open_when_ready, args=(open_url,), daemon=True).start()

    procfile_path = _find_procfile(worktree_path)
    if procfile_path:
        _run_procfile(worktree_path, procfile_path, extra_env)
        return

    run_cmd = entry.run_cmd or cfg.get("run_cmd")
    if run_cmd:
        _run_single_command(worktree_path, run_cmd, extra_env)
        return

    print(f"[bridge] Nothing to run: no Procfile.dev/Procfile in {worktree_path}, "
          f"and no run_cmd configured for '{repo_name}' in {TOML_FILE}.")
    print("[bridge] Add a Procfile.dev to the repo, or set run_cmd under [repos] "
          "or as a top-level fallback.")


_BRANCH_CARD_ID_RE = re.compile(r"^qtask/(\d+)")


def _extract_card_id_from_branch(branch):
    """Return the card id encoded in a qtask branch name ('qtask/84-foo' ->
    84), or None if the branch doesn't look like one _create_worktree made."""
    m = _BRANCH_CARD_ID_RE.match(branch)
    return int(m.group(1)) if m else None


def _fetch_job_context_for_branch(cfg, branch):
    """Best-effort recovery of the job that produced this worktree, for
    --review: BRIDGE_SPEC.md is deleted from the worktree once the job ends
    (see run_job's finally block), but the spec is still on the server
    forever as BridgeJob.spec_snapshot, and the branch name itself encodes
    the card id -- so it can be recovered via the *existing*
    /api/bridge/jobs/card/{id}/latest endpoint, no new backend route needed.

    Returns (spec_snapshot, result) or (None, None). This is enrichment, not
    a requirement: a network failure, missing job, or a branch_name mismatch
    (e.g. a stale retry for the same card) must never block the review from
    running -- so every failure mode here degrades to (None, None) rather
    than raising. api() itself only catches HTTPError, not connection
    failures/timeouts, hence the broad except.
    """
    card_id = _extract_card_id_from_branch(branch)
    if card_id is None:
        return None, None
    try:
        resp = api(cfg, "GET", f"/api/bridge/jobs/card/{card_id}/latest")
        job = resp.get("job") if resp else None
        if not job or job.get("branch_name") != branch:
            return None, None
        return job.get("spec_snapshot"), job.get("result")
    except Exception:
        return None, None


def _make_review_prompt(spec_text, verification_text):
    """Build a read-only, lead-engineer-style review prompt: assumptions,
    code quality, duplication, anti-patterns, test coverage -- a different
    question from test_cmd ("does it work") or verify_acceptance ("does it
    meet the spec's acceptance criteria"), both of which already exist.
    Explicitly forbidden from modifying anything, same posture as
    _make_acceptance_check_prompt and for the same reason: keeps "review"
    cleanly separated from "fix" (fixing automatically is still deferred)."""
    parts = [
        "Review your changes in this worktree (git diff against the primary branch) "
        "the way a careful lead engineer would before approving a pull request. Look for:",
        "  - Incorrect or unstated assumptions the implementation makes",
        "  - Code quality issues",
        "  - Duplicate code or logic that should be consolidated",
        "  - Anti-patterns",
        "  - Missing or inadequate test coverage",
        "  - Anything else worth flagging before this gets merged",
        "",
        "Do not modify, create, or delete any files -- this is a read-only review, not "
        "an implementation task. For each issue, give a short description and the "
        "file/location. If the code genuinely looks solid, say so plainly rather than "
        "manufacturing nitpicks.",
    ]
    if spec_text:
        parts += ["", "## Original Task Spec", spec_text]
    if verification_text:
        parts += ["", "## Automated Verification Results", verification_text]
    return "\n".join(parts)


def _make_review_followup_prompt(review_output):
    """Build the prompt for the optional interactive follow-up after a
    read-only review: hands the agent its own just-printed findings back
    as context (this is a fresh process -- nothing else carries them over)
    and asks it to act on them, same posture as _make_prompt's normal job
    prompts (discuss ambiguity rather than guessing)."""
    return (
        "You just performed the following read-only code review of your own "
        "changes in this worktree:\n\n"
        f"{review_output}\n\n"
        "Apply the fixes/improvements identified above. If any point is "
        "ambiguous, or you're unsure whether a suggestion should actually be "
        "applied, ask before proceeding rather than guessing."
    )


def cmd_review(cfg, target):
    """Read-only lead-engineer-style review of a resolved qtask worktree's
    changes, run on demand from the command line -- the deliberately
    scoped-down first step of the self-review pass: manual, not
    server-triggered, and reports only, never fixes on its own. Streams
    output live (Popen + line-by-line print, like _run_streaming) rather
    than the blocking capture_output=True pattern _check_acceptance_criteria
    uses -- that pattern is fine for a background job nobody's watching
    live, but a human sitting at this terminal waiting on the result needs
    to see it arrive, not stare at a silent pause for 30-60+ seconds.

    Once the review finishes, offers to launch an interactive follow-up
    session (same worktree) with the review's own findings handed back as
    context, so applying them doesn't require the user to re-explain what
    was just found in a separate, context-free session. Still opt-in --
    declining leaves the worktree untouched, same as today.

    Disables remote push for the duration (review pass AND the apply
    follow-up), same as run_job -- by the time you run --review, the
    ORIGINAL job's own _git_teardown has almost always already restored
    push (that's the normal "job finished, now go check it" sequence this
    command exists for), so without this the apply-changes follow-up would
    launch a real, file-writing coding agent session with push fully
    enabled. Covers the review pass too, not just the follow-up -- same
    "don't just trust the prompt" reasoning _disable_remote_push exists for
    everywhere else, even though the review prompt itself already says
    read-only."""
    resolved = _resolve_worktree_target(cfg, target)
    if resolved is None:
        return
    repo_name, work_dir, worktree_path, branch = resolved
    print(f"[bridge] [{repo_name}] {branch}\n[bridge] {worktree_path}\n")

    spec_text, verification_text = _fetch_job_context_for_branch(cfg, branch)
    prompt = _make_review_prompt(spec_text, verification_text)

    push_url_info = _disable_remote_push(work_dir)
    try:
        print("[bridge] Reviewing (read-only)...\n")
        try:
            proc = subprocess.Popen(
                streaming_command(prompt), cwd=worktree_path,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except FileNotFoundError:
            print(f"[bridge] ERROR: '{AGENT_LABEL}' not found.", file=sys.stderr)
            print(f"[bridge]   {AGENT_NOT_FOUND_HINT}", file=sys.stderr)
            return

        output_lines = []
        for line in proc.stdout:
            print(line.rstrip("\n"))
            output_lines.append(line.rstrip("\n"))
        proc.wait()
        print(f"\n[bridge] Review finished (exit {proc.returncode}).")

        if proc.returncode != 0:
            return

        try:
            answer = input("[bridge] Apply these changes now? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt, OSError):
            # OSError covers non-interactive stdin (e.g. piped/redirected input,
            # or a test harness capturing stdin) -- same "can't ask, so don't
            # apply" outcome as an explicit decline.
            print()
            return
        if answer not in ("y", "yes"):
            print("[bridge] No changes applied. Worktree left as-is.")
            return

        followup_prompt = _make_review_followup_prompt("\n".join(output_lines))
        print(f"\n[bridge] Launching {AGENT_LABEL} interactively to apply the review...\n")
        try:
            try:
                subprocess.run(interactive_command(followup_prompt), cwd=worktree_path, check=False)
            except FileNotFoundError:
                print(f"[bridge] ERROR: '{AGENT_LABEL}' not found.", file=sys.stderr)
                print(f"[bridge]   {AGENT_NOT_FOUND_HINT}", file=sys.stderr)
        finally:
            # Phase C (CLAUDE_CODE_INTEGRATION.md): shares run_job's commit safety net, not
            # the full job-queue mechanism -- this apply session has no BridgeJob of its own
            # (job_id=None), deliberately, so it works even for a worktree whose branch can't
            # be traced back to a card at all (e.g. a Phase 1 custom name with no matching
            # job). In the finally so an interrupt during the apply session doesn't skip it,
            # same reasoning as _run_interactive/_run_streaming's own end-of-session call.
            # Scoped to just the apply step, not the whole function -- the read-only review
            # pass and an explicit decline should still leave the worktree genuinely
            # untouched, per this function's own docstring.
            _commit_if_dirty(worktree_path, None, "review apply")
    finally:
        _git_teardown(work_dir, push_url_info)


def cmd_rename_branch(cfg, new_name):
    """Rename the git branch for the current (cwd) or last-used qtask worktree, and update
    the app's recorded branch_name to match -- for when you forgot to set a branch name at
    queue time, want to rename one later, or the two have drifted because the branch was
    renamed via raw git without going through this command (branch_name is otherwise only
    ever written once, by /start, at session-start time -- nothing else keeps it in sync).

    Resolution is cwd-or-last only, same as --run/--review with no target given -- not
    fragment-matching against an arbitrary worktree, since the new name is already this
    command's one required argument and a second "which worktree" argument would be an
    awkward CLI shape. cd (or `qcd`) to the worktree you mean first if it isn't already
    cwd/last-used."""
    new_name = new_name.strip()
    if not new_name:
        print("[bridge] New branch name can't be empty.", file=sys.stderr)
        return
    if any(c.isspace() for c in new_name):
        print("[bridge] New branch name can't contain whitespace.", file=sys.stderr)
        return
    if new_name.startswith("-"):
        # git branch -m passes this straight into its own argv -- see create_job's identical
        # concern for the same reasoning.
        print("[bridge] New branch name can't start with '-'.", file=sys.stderr)
        return

    resolved = _resolve_worktree_target(cfg, "")
    if resolved is None:
        return
    repo_name, work_dir, worktree_path, old_branch = resolved

    if new_name == old_branch:
        print(f"[bridge] Already named {new_name!r} -- nothing to do.")
        return

    # Refuse if the job currently using this worktree is still actively running -- renaming
    # the branch out from under a live agent session is asking for a confusing mid-session
    # surprise, same reasoning as --adopt's identical guard.
    resp = api(cfg, "GET", f"/api/bridge/jobs/by-worktree?path={urllib.parse.quote(worktree_path)}")
    latest = resp.get("job") if resp else None
    if latest and latest["status"] in ("pending", "running"):
        print(f"[bridge] Job {latest['id']} for this worktree is still {latest['status']} -- "
              f"wait for it to finish (or stall/error out) before renaming its branch.", file=sys.stderr)
        return

    r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{new_name}"],
                       cwd=worktree_path, capture_output=True)
    if r.returncode == 0:
        print(f"[bridge] A branch named {new_name!r} already exists in this repo.", file=sys.stderr)
        return

    r = subprocess.run(["git", "branch", "-m", old_branch, new_name],
                       cwd=worktree_path, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[bridge] git branch rename failed: {r.stderr.strip()}", file=sys.stderr)
        return
    print(f"[bridge] [{repo_name}] Renamed branch {old_branch!r} -> {new_name!r}.")

    if latest is None:
        print("[bridge] Renamed locally, but no bridge job record found for this worktree "
              "to update -- nothing more to do.")
        return
    api(cfg, "POST", f"/api/bridge/jobs/{latest['id']}/rename-branch", {"branch_name": new_name})
    print(f"[bridge] Updated the app's record for job #{latest['id']}.")


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
    group.add_argument("--adopt", action="store_true",
                       help="Detach a qtask worktree from its branch and check the branch "
                            "out in the primary checkout instead, so you can keep working "
                            "on it there directly -- reversible via a later --fix/--resume "
                            "on the same job")
    group.add_argument("--list", action="store_true",
                       help="List qtask worktrees across configured repos (read-only, no prompt)")
    group.add_argument("--switch", action="store_true",
                       help="Interactive menu of qtask worktrees for the current repo "
                            "(most recent first); prints the chosen path on stdout only, "
                            "for `cd \"$(qtask-bridge --switch)\"`")
    group.add_argument("--run", nargs="?", const="", default=None, metavar="[BRANCH]",
                       help="Run the app in a resolved qtask worktree (cwd, last one, or a "
                            "branch fragment) via its Procfile.dev/Procfile or configured run_cmd")
    group.add_argument("--review", nargs="?", const="", default=None, metavar="[BRANCH]",
                       help="Read-only lead-engineer-style review of a qtask worktree's "
                            "changes (cwd, last one, or a branch fragment)")
    group.add_argument("--unlock-push", action="store_true",
                       help="Clear a stuck no_push sentinel on the current repo's "
                            "remote.origin.pushurl, left behind by an interrupted job "
                            "(operates on cwd's repo, not a specific worktree)")
    group.add_argument("--lock-push", action="store_true",
                       help="Manually set the no_push sentinel on the current repo's "
                            "remote.origin.pushurl, the same safety a job's session gets "
                            "automatically (operates on cwd's repo, not a specific worktree)")
    group.add_argument("--rename-branch", metavar="NEW_NAME", default=None,
                       help="Rename the git branch for the current (cwd) or last-used qtask "
                            "worktree, and update the app's recorded branch_name to match")
    parser.add_argument("--agent", metavar="NAME", default=None,
                       help="Use this coding agent for just this run, overriding "
                            "config.toml's \"agent\" key (default: claude). Combine with "
                            "any command above, e.g. `qtask-bridge --card 84 --agent aider`.")
    args = parser.parse_args()

    cfg = load_config()
    _activate_adapter(args.agent or cfg.get("agent") or _DEFAULT_AGENT)
    if args.watch:
        cmd_watch(cfg)
    elif args.tag:
        cmd_tag(cfg, args.tag)
    elif args.cleanup:
        cmd_cleanup(cfg)
    elif args.adopt:
        cmd_adopt(cfg)
    elif args.list:
        cmd_list(cfg)
    elif args.switch:
        cmd_switch(cfg)
    elif args.run is not None:
        cmd_run(cfg, args.run or None)
    elif args.review is not None:
        cmd_review(cfg, args.review or None)
    elif args.unlock_push:
        cmd_unlock_push()
    elif args.lock_push:
        cmd_lock_push()
    elif args.rename_branch is not None:
        cmd_rename_branch(cfg, args.rename_branch)
    else:
        cmd_card(cfg, args.card)


# No `if __name__ == "__main__": main()` guard here, deliberately -- see
# render.py's render_agent_script(). This file's main() must not fire until
# AFTER the adapter file's definitions have executed, and since this file
# is concatenated first (its shebang must be the served script's literal
# first line), a guard here would call main() before the adapter's names
# even exist, breaking anything that touches interactive_command/
# streaming_command/write_ide_settings/AGENT_LABEL/AGENT_NOT_FOUND_HINT.
# render.py appends the guard itself, once, after both files are joined.
