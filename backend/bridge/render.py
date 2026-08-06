"""
Renders the served qtask-bridge CLI scripts from real files in bridge/scripts/.

install.py is served with two literal sentinel placeholders substituted via
plain str.replace() (not an f-string/.format() — this is what lets install.py
freely contain braces, e.g. dict literals and its own f-strings, without any
escaping). agent.py is served as a request-time textual concatenation of the
Claude adapter (agent_claude.py) and the agent-agnostic core (agent_core.py)
— see the module docstring in agent_core.py for why this is concatenation,
not an import, and what the adapter contract is.
"""
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent / "scripts"

_APP_URL_PLACEHOLDER = "__QTASK_APP_URL__"
_TOKEN_PLACEHOLDER = "__QTASK_TOKEN__"


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
    # Neither source file carries its own `if __name__ == "__main__":
    # main()` guard -- that entrypoint call is appended here, ONCE, after
    # both are joined. It must come after BOTH files' content: it fires
    # immediately at module-exec time (unlike ordinary function calls,
    # which only resolve names when actually invoked), so if it lived
    # inside agent_core.py's own source -- which is textually first, for
    # the shebang reason above -- main() would run before agent_claude.py's
    # definitions two lines below it had ever executed. That exact mistake
    # shipped a real `NameError: name 'write_ide_settings' is not defined`
    # to a live machine once already; don't reintroduce it by moving the
    # guard back into either individual file.
    core = (_SCRIPTS_DIR / "agent_core.py").read_text()
    adapter = (_SCRIPTS_DIR / "agent_claude.py").read_text()
    return core + "\n\n" + adapter + "\n\nif __name__ == \"__main__\":\n    main()\n"
