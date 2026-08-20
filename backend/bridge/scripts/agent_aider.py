"""
Aider adapter for the qtask-bridge agent -- the first non-Claude adapter (Phase 3 of
BRIDGE_MULTI_AGENT_SUPPORT.md). Implements the same six-name contract agent_claude.py does
(see agent_core.py's module docstring); every name here is suffixed `__aider`.

Flags verified against aider's official docs (aider.chat/docs/config/options.html and
aider.chat/docs/scripting.html) as of 2026-08, not just recalled from training data --
aider's CLI surface has changed across versions before (older docs reference a bare `--yes`
that current docs no longer list, only `--yes-always`), so this is worth re-checking against
whatever aider version is actually installed if streaming jobs ever start hanging or
prompting unexpectedly. This has NOT been hands-on tested against a real installed aider
binary the way agent_claude.py's flow is covered by TestRealInstalledBinary -- see
BRIDGE_MULTI_AGENT_SUPPORT.md's Phase 3 Open Questions for what that verification pass
should check before this is fully trusted for unattended --watch/--tag use.

Known real gap vs. agent_claude.py, partially mitigated: Claude Code's interactive_command
seeds the session with the task prompt as a positional arg and then stays interactive
(`claude "<prompt>"`). Aider has no equivalent -- its one-shot flag (`--message`) explicitly
processes the message THEN EXITS ("process reply then exit (disables chat mode)"), so there's
no documented way to seed a chat message and remain in the interactive REPL afterward. What
aider DOES have (verified: aider.chat/docs/config/options.html, "--load LOAD_FILE: Load and
execute /commands from a file on launch") is a way to run slash-commands on startup before
dropping into the normal REPL -- interactive_command__aider uses this to auto-run `/read
BRIDGE_SPEC.md`, so the spec is in context immediately without the user typing it themselves,
even though the actual prompt text still can't be auto-sent as a chat message the way
`["claude", prompt]` can. The load-file's content ("BRIDGE_SPEC.md") duplicates
agent_core.py's SPEC_FILENAME constant as a literal string rather than referencing it by name
across files -- deliberately avoids a new implicit cross-file dependency for a minor
convenience feature (this codebase has shipped two real NameError incidents from concatenation-
order surprises already; not worth a third source of one for this).
"""
import tempfile

AGENT_LABEL__aider = "Aider"
AGENT_NOT_FOUND_HINT__aider = "python -m pip install aider-install && aider-install"
# Aider has no local per-worktree IDE settings file comparable to Claude Code's statusline --
# nothing for write_ide_settings__aider to write, so nothing to gitignore either.
IDE_SETTINGS_GITIGNORE_ENTRY__aider = None


def interactive_command__aider(prompt):
    # See module docstring: the prompt argument itself is unused (no documented way to seed
    # a chat message and stay interactive) -- --load at least auto-reads BRIDGE_SPEC.md into
    # context on startup. delete=False: the file must still exist on disk when the *next*
    # process (aider itself) opens it -- closing a delete=True NamedTemporaryFile removes it
    # immediately. Left behind in the OS temp dir afterward, not cleaned up -- one tiny text
    # file per interactive session, accepted as harmless litter rather than adding adapter
    # cleanup machinery for it.
    load_file = tempfile.NamedTemporaryFile(
        mode="w", prefix="qtask-aider-load-", suffix=".md", delete=False
    )
    load_file.write("/read BRIDGE_SPEC.md\n")
    load_file.close()
    return ["aider", "--load", load_file.name]


def streaming_command__aider(prompt):
    return [
        "aider",
        "--message", prompt,
        "--yes-always",       # auto-approve every edit/confirmation -- aider's equivalent of
                               # Claude's --dangerously-skip-permissions
        "--auto-commits",     # explicit, not just relying on aider's own default, in case a
                               # user's ~/.aider.conf.yml overrides it
        "--no-check-update",     # these avoid every other documented source of a startup
        "--no-analytics",        # prompt/blocking network call -- a --watch/--tag job has no
        "--no-detect-urls",      # attached stdin to answer any of them if they fired
        "--disable-playwright",
    ]


def write_ide_settings__aider(worktree_path):
    # No-op -- see IDE_SETTINGS_GITIGNORE_ENTRY__aider above.
    pass
