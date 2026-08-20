"""
Renders the served qtask-bridge CLI scripts from real files in bridge/scripts/.

install.py is served with two literal sentinel placeholders substituted via
plain str.replace() (not an f-string/.format() — this is what lets install.py
freely contain braces, e.g. dict literals and its own f-strings, without any
escaping). agent.py is served as a request-time textual concatenation of the
agent-agnostic core (agent_core.py) and EVERY known coding-agent adapter
(_ADAPTER_FILES below) — see the module docstring in agent_core.py for why
concatenation, not import, what the adapter contract is, and why each adapter's five
contract names carry a `__{agent}` suffix so multiple adapters can coexist in one
module namespace. Which adapter is actually active on a given machine is decided at
runtime by agent_core.py's `_activate_adapter()`, reading config.toml's "agent" key —
not by this file, which always concatenates all of them.
"""
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent / "scripts"

_APP_URL_PLACEHOLDER = "__QTASK_APP_URL__"
_TOKEN_PLACEHOLDER = "__QTASK_TOKEN__"

# agent name (config.toml's "agent" key) -> adapter source file in scripts/. Every served
# script carries every adapter here; _activate_adapter() picks one at runtime. Add a new
# coding agent by writing scripts/agent_<name>.py implementing the suffixed five-name
# contract (see agent_claude.py) and adding it here.
_ADAPTER_FILES = {
    "claude": "agent_claude.py",
    "aider": "agent_aider.py",
}


def render_install_script(app_url: str, token: str) -> str:
    source = (_SCRIPTS_DIR / "install.py").read_text()
    source = source.replace(_APP_URL_PLACEHOLDER, app_url)
    source = source.replace(_TOKEN_PLACEHOLDER, token)
    return source


def render_agent_script() -> str:
    # agent_core.py must come first: its #!/usr/bin/env python3 shebang has
    # to be the served file's literal first line, or the installer's chmod
    # +x'd copy at ~/.local/bin/qtask-bridge has no valid interpreter
    # directive to execute with -- the shell falls back to interpreting the
    # whole file as its own script instead, producing garbage like
    # "import: command not found".
    #
    # No source file here carries its own `if __name__ == "__main__":
    # main()` guard -- that entrypoint call is appended here, ONCE, after
    # every file is joined. It must come after ALL of them: it fires
    # immediately at module-exec time (unlike ordinary function calls,
    # which only resolve names when actually invoked), so if it lived
    # inside agent_core.py's own source -- which is textually first, for
    # the shebang reason above -- main() would run before any adapter's
    # definitions, textually after it, had ever executed. That exact mistake
    # shipped a real `NameError: name 'write_ide_settings' is not defined`
    # to a live machine once already; don't reintroduce it by moving the
    # guard back into any individual file. (Definition order among the
    # adapters themselves, and between adapters and agent_core.py, doesn't
    # matter beyond that -- _activate_adapter() resolves names at call
    # time, inside main(), well after every top-level statement in every
    # concatenated file has already run.)
    core = (_SCRIPTS_DIR / "agent_core.py").read_text()
    adapters = "\n\n".join(
        (_SCRIPTS_DIR / filename).read_text()
        for filename in _ADAPTER_FILES.values()
    )
    return core + "\n\n" + adapters + "\n\nif __name__ == \"__main__\":\n    main()\n"
