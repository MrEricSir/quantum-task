"""
Claude Code adapter for the qtask-bridge agent.

Implements the adapter contract agent_core.py expects from any coding-agent adapter (see the
comment at the top of agent_core.py): AGENT_LABEL, AGENT_NOT_FOUND_HINT,
interactive_command(), streaming_command(), write_ide_settings(). Every name is suffixed
`__claude` here rather than bare -- multiple adapters get concatenated into one served script
(see bridge/render.py), so each adapter's names need to coexist in one module namespace
without colliding. agent_core.py's `_activate_adapter()` aliases the bare contract names
(AGENT_LABEL, interactive_command, etc.) to whichever suffix `config.toml`'s "agent" key
selects, at the top of main() -- nothing in this file is referenced by its bare (unsuffixed)
name from outside it.

To add a different coding agent, write a sibling file (e.g. agent_aider.py) implementing the
same five names with a different suffix, and add it to bridge/render.py's _ADAPTER_FILES.
"""
import json
import os

AGENT_LABEL__claude = "Claude Code"
AGENT_NOT_FOUND_HINT__claude = "npm install -g @anthropic-ai/claude-code"


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
