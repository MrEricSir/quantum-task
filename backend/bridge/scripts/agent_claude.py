"""
Claude Code adapter for the qtask-bridge agent.

Implements the adapter contract agent_core.py expects from any coding-agent adapter (see the
comment at the top of agent_core.py): AGENT_LABEL, AGENT_NOT_FOUND_HINT,
IDE_SETTINGS_GITIGNORE_ENTRY, interactive_command(), streaming_command(),
write_ide_settings(). Every name is suffixed `__claude` here rather than bare -- multiple
adapters get concatenated into one served script (see bridge/render.py), so each adapter's
names need to coexist in one module namespace without colliding. agent_core.py's
`_activate_adapter()` aliases the five CLI-relevant bare contract names (AGENT_LABEL,
interactive_command, etc.) to whichever suffix `config.toml`'s "agent" key selects, at the top
of main() -- nothing in this file is referenced by its bare (unsuffixed) name from outside it.
IDE_SETTINGS_GITIGNORE_ENTRY is the one contract member `_activate_adapter()` aliases but
nothing at CLI runtime actually reads -- its real consumer is install-time gitignore setup
(see install.py's BRIDGE_IGNORE_ENTRIES), which currently lists it by hand rather than
deriving it automatically; see BRIDGE_MULTI_AGENT_SUPPORT.md's Phase 2 for why.

To add a different coding agent, write a sibling file (e.g. agent_aider.py) implementing the
same six names with a different suffix, add it to bridge/render.py's _ADAPTER_FILES, and add
its IDE_SETTINGS_GITIGNORE_ENTRY value (if not None) to install.py's BRIDGE_IGNORE_ENTRIES.
"""
import json
import os

AGENT_LABEL__claude = "Claude Code"
AGENT_NOT_FOUND_HINT__claude = "npm install -g @anthropic-ai/claude-code"
# Path write_ide_settings__claude() writes, relative to the worktree root -- installed
# globally into git's core.excludesFile by install.py's BRIDGE_IGNORE_ENTRIES so it's never
# accidentally committed. None here would mean "this adapter writes no IDE-specific file."
IDE_SETTINGS_GITIGNORE_ENTRY__claude = ".claude/settings.local.json"


def interactive_command__claude(prompt):
    return ["claude", prompt]


def streaming_command__claude(prompt):
    return ["claude", "--print", "--dangerously-skip-permissions", prompt]


def write_ide_settings__claude(worktree_path):
    """Write a local (gitignored) Claude Code settings file into the
    worktree configuring a status line that shows the branch and path
    for the whole session — the point being you should never have to
    wonder which worktree you're in while Claude is running. Dynamic
    (shells out to git/pwd at render time) rather than baking in the
    known values, so it stays correct even if the worktree is later
    moved or renamed."""
    claude_dir = os.path.join(worktree_path, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    settings_path = os.path.join(claude_dir, "settings.local.json")
    settings = {
        "statusLine": {
            "type": "command",
            "command": 'echo "[qtask] $(git branch --show-current) · $(pwd)"',
        }
    }
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
