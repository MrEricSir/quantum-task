#!/usr/bin/env python3
"""
qtask-bridge installer
Installs the qtask-bridge CLI with your app URL and token pre-configured.

Served via GET /api/bridge/install.py (see backend/bridge/render.py), which
substitutes the two sentinel placeholders below with the real app URL and a
pre-authed token before sending the response — plain str.replace(), not an
f-string, so nothing in this file ever needs brace-escaping.
"""
import os, sys, stat, ssl, subprocess, textwrap, urllib.error, urllib.request, json

APP_URL = "__QTASK_APP_URL__"
TOKEN   = "__QTASK_TOKEN__"
INSTALL_DIR = os.path.expanduser("~/.local/bin")
CONFIG_DIR  = os.path.expanduser("~/.config/qtask-bridge")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TOML_FILE   = os.path.join(CONFIG_DIR, "config.toml")
BRIDGE_PATH = os.path.join(INSTALL_DIR, "qtask-bridge")

# Indented consistently with the rest of this script (unlike a
# column-0 literal) so textwrap.dedent() actually has something
# uniform to strip — both here at generation time and again below
# at install time, when it's re-dedented back down to column 0
# before being written to config.toml.
TOML_TEMPLATE = textwrap.dedent("""\
    # qtask-bridge configuration

    # Friendly name shown in notifications and the job status panel.
    # Defaults to your hostname if left empty.
    name = ""

    # Which coding agent to launch. Defaults to "claude" if unset. Currently supported:
    # "claude" (Claude Code) or "aider". Every served qtask-bridge binary carries every
    # adapter it was built with, so switching here takes effect on your next run -- no
    # reinstall needed, as long as this binary was built with the agent you're switching to.
    #
    # agent = "aider"

    # Map repo slugs to local checkout paths. Either a plain path string,
    # or a table with "path" and optional "setup_cmd" / "test_cmd" /
    # "verify_acceptance" / "run_cmd" / "env_files" keys. setup_cmd is a
    # one-time command run in a fresh worktree before Claude launches, for
    # repos that need dependencies installed (npm install, pip install, etc).
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

    # env_files: paths (relative to the repo root) to symlink from your base
    # checkout into every fresh worktree -- `git worktree add` only ever
    # checks out tracked files, and .env files are gitignored by
    # definition, so without this a worktree has no real secrets/config to
    # run with. Symlinked, not copied, so there's only ever one real copy
    # on disk and it can't drift. Names/locations vary per repo (this is
    # the one setting here that's almost never worth a top-level fallback),
    # so set it per-repo:
    #
    # [repos."owner/myapp"]
    # path = "/Users/you/code/myapp"
    # env_files = ["backend/.env", "frontend/.env"]

    # run_cmd is what `qtask-bridge --run` uses to start the app in a
    # worktree for manual testing -- set per-repo (see [repos] above) or as
    # a fallback here. Only used when the worktree has no Procfile.dev or
    # Procfile: if either is present, --run uses it instead (starting every
    # process it lists concurrently, e.g. a separate frontend and backend),
    # since that's a stronger signal for repos with more than one process
    # that needs to run together.
    #
    # run_cmd = "npm run dev"

    # open_url: if set, `qtask-bridge --run` opens it in your default browser
    # once the dev server actually responds (polls briefly, opens anyway
    # after ~10s even if it never does, rather than silently doing nothing).
    # A shell-evaluated string, not a plain literal -- reference the reserved
    # port from .env.qtask (see "Avoiding port and database collisions" in
    # the README) and do any arithmetic your repo's Procfile.dev needs
    # (e.g. frontend on port+1) with real shell syntax:
    #
    # open_url = "http://localhost:$((QTASK_PORT_BASE + 1))"
    #
    # QTASK_PORT_BASE alone usually isn't "the" port to guess automatically
    # -- this repo's own Procfile.dev puts the backend there and the
    # frontend one above it -- so this is deliberately explicit per-repo,
    # not an automatic default.

    # Verification, run automatically after a session ends (before the job
    # is marked complete) -- both are opt-in and off by default.
    #
    # test_cmd runs your test suite and includes pass/fail in the job
    # result -- no LLM call, purely mechanical. Set per-repo (see [repos]
    # above) or as a fallback here:
    #
    # test_cmd = "npm test"
    #
    # verify_acceptance runs one extra, read-only Claude check of the diff
    # against the spec's Acceptance Criteria checklist, reporting MET/NOT
    # MET per item. Costs one extra LLM call per job, so it's off unless
    # set (per-repo, or as a fallback here):
    #
    # verify_acceptance = true

    # Alternatively, list root directories and the bridge will discover repos by
    # scanning for matching .git remotes automatically. Auto-discovered repos
    # don't get a setup_cmd — configure those explicitly under [repos] instead.
    #
    # repo_roots = ["~/code", "~/work"]
""")

# Files qtask-bridge writes into every worktree it creates -- BRIDGE_SPEC.md
# (agent_core.py's SPEC_FILENAME), .env.qtask (agent_core.py's ENV_FILENAME),
# .qtask-worktree (agent_core.py's WORKTREE_MARKER_FILENAME -- empty provenance marker so
# _scan_qtask_worktrees can find a worktree regardless of its branch name), plus one
# entry per adapter's IDE_SETTINGS_GITIGNORE_ENTRY__<name> for whatever IDE config
# write_ide_settings() writes (.claude/settings.local.json is agent_claude.py's --
# IDE_SETTINGS_GITIGNORE_ENTRY__claude). Kept in sync by hand, checked against every
# adapter in render.py's _ADAPTER_FILES by
# TestAdapterGitignoreEntriesStaySynced (tests/test_bridge_scripts.py) -- add a new
# adapter's entry here (if not None) when adding it to _ADAPTER_FILES, or that test fails.
BRIDGE_IGNORE_ENTRIES = [
    "BRIDGE_SPEC.md", ".env.qtask", ".qtask-worktree", ".claude/settings.local.json",
]


def setup_global_gitignore():
    """Ignore qtask-bridge's worktree-local files globally, via git's
    own core.excludesFile mechanism, instead of ever touching a target
    repo's own .gitignore. Runs at install time (not per-job) since
    it's a one-time, machine-wide setting -- re-running the installer
    is idempotent and safe if it's already set up."""
    result = subprocess.run(
        ["git", "config", "--global", "--get", "core.excludesFile"],
        capture_output=True, text=True,
    )
    excludes_path = result.stdout.strip()
    if excludes_path:
        excludes_path = os.path.expanduser(excludes_path)
    else:
        # Nothing configured yet -- use our own file rather than
        # guessing at a platform default, and point git at it.
        excludes_path = os.path.expanduser("~/.config/git/ignore_qtask_bridge")
        os.makedirs(os.path.dirname(excludes_path), exist_ok=True)
        subprocess.run(["git", "config", "--global", "core.excludesFile", excludes_path])
        print(f"Set git core.excludesFile: {excludes_path}")

    existing = ""
    if os.path.exists(excludes_path):
        with open(excludes_path) as f:
            existing = f.read()
    missing = [e for e in BRIDGE_IGNORE_ENTRIES if e not in existing]
    if not missing:
        print(f"Global gitignore already covers qtask-bridge files: {excludes_path}")
        return
    os.makedirs(os.path.dirname(excludes_path), exist_ok=True)
    with open(excludes_path, "a") as f:
        for entry in missing:
            f.write("\n" + entry + "\n")
    print(f"Updated global gitignore: {excludes_path}")


def main():
    # Download the bridge script from the app
    print("Downloading qtask-bridge...")
    req = urllib.request.Request(
        f"{APP_URL}/api/bridge/agent.py",
        headers={"Authorization": f"Bearer {TOKEN}"},
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

    config = {"app_url": APP_URL, "token": TOKEN}
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    # Write config.toml only if it doesn't exist — preserve user edits on reinstall
    if not os.path.exists(TOML_FILE):
        with open(TOML_FILE, "w") as f:
            f.write(TOML_TEMPLATE)
        print(f"Created:   {TOML_FILE}")
    else:
        print(f"Kept:      {TOML_FILE}  (already exists)")

    print(f"Installed: {BRIDGE_PATH}")
    print(f"Config:    {CONFIG_FILE}")

    setup_global_gitignore()

    # Resolve the shell rc file once -- used below both to add ~/.local/bin to
    # PATH (if missing) and to install the qcd() shell function (always, if
    # not already present). A subprocess can never change its parent shell's
    # cwd -- that's an OS-level constraint, not something --switch itself can
    # work around -- so qcd() is the actual "switch directories" mechanism;
    # --switch only prints the chosen path. Auto-installing qcd() here means
    # that mechanism works with zero manual copy-pasting, not just the PATH line.
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        rc = os.path.expanduser("~/.zshrc")
    elif "bash" in shell:
        rc = os.path.expanduser("~/.bash_profile")
    else:
        rc = None

    path_export = 'export PATH="$HOME/.local/bin:$PATH"'
    if INSTALL_DIR not in os.environ.get("PATH", "").split(os.pathsep):
        if rc:
            try:
                existing = open(rc).read() if os.path.exists(rc) else ""
                if ".local/bin" not in existing:
                    with open(rc, "a") as rf:
                        rf.write("\n# Added by qtask-bridge installer\n" + path_export + "\n")
                    print("Added ~/.local/bin to PATH in " + rc)
            except OSError as e:
                print("Could not update " + rc + ": " + str(e))
                print("Add manually: " + path_export)
        else:
            print("\nAdd to your shell config: " + path_export)

    qcd_marker = "qcd() {"
    qcd_snippet = (
        "\n# Added by qtask-bridge installer -- switches to a worktree picked\n"
        "# from `qtask-bridge --switch`'s menu (a subprocess can't cd its\n"
        "# parent shell itself, so this wrapper does it).\n"
        "qcd() {\n"
        '  local wt; wt="$(qtask-bridge --switch)"\n'
        '  [ -n "$wt" ] && cd "$wt"\n'
        "}\n"
    )
    qcd_installed = False
    if rc:
        try:
            existing = open(rc).read() if os.path.exists(rc) else ""
            if qcd_marker not in existing:
                with open(rc, "a") as rf:
                    rf.write(qcd_snippet)
                print("Added qcd() shell function to " + rc)
                qcd_installed = True
            else:
                print("qcd() already present in " + rc + " -- left as-is")
        except OSError as e:
            print("Could not update " + rc + ": " + str(e))
            print("Add manually:" + qcd_snippet)
    else:
        print("\nCouldn't detect your shell to install qcd() automatically -- add manually:")
        print(qcd_snippet)

    print()
    print("Usage (run from your repo directory):")
    print("  qtask-bridge --card <card-id>   # run a specific card's job")
    print("  qtask-bridge --watch            # poll for jobs automatically")
    print("  qtask-bridge --tag <name>       # run every pending-spec card with this tag")
    print("  qtask-bridge --list             # list qtask worktrees (read-only)")
    print("  qtask-bridge --switch           # menu of worktrees for the current repo, most recent first")
    print("  qtask-bridge --cleanup          # list/remove finished qtask worktrees")
    print("  qtask-bridge --run [branch]     # run the app in a qtask worktree (cwd, last one, or a branch fragment)")
    print("  qtask-bridge --review [branch]  # read-only lead-engineer-style review of a worktree's changes")
    print("  qtask-bridge --unlock-push      # clear a stuck no_push sentinel left by an interrupted job")
    print("  qtask-bridge --lock-push        # manually set the no_push sentinel, same safety a job gets automatically")
    print()
    print("  qcd                             # menu-pick a worktree and actually cd into it")
    if qcd_installed or rc:
        print("(source " + (rc or "your shell config") + ", or open a new terminal, to start using qcd)")


if __name__ == "__main__":
    main()
