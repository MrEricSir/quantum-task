"""
Claude Code adapter for the qtask-bridge agent.

Implements the small contract agent_core.py expects from any coding-agent
adapter (see the comment at the top of agent_core.py): AGENT_LABEL,
AGENT_NOT_FOUND_HINT, interactive_command(), streaming_command(),
write_ide_settings(). Nothing else in this file is referenced by name from
outside it.

To try a different coding agent, write a sibling file implementing the same
five names and point backend/bridge/render.py at it instead of this one.
"""
import json
import os

AGENT_LABEL = "Claude Code"
AGENT_NOT_FOUND_HINT = "npm install -g @anthropic-ai/claude-code"


def interactive_command(prompt):
    return ["claude", prompt]


def streaming_command(prompt):
    return ["claude", "--print", "--dangerously-skip-permissions", prompt]


def write_ide_settings(worktree_path):
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
