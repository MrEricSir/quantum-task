"""
Tests for the served qtask-bridge CLI scripts (bridge/render.py, bridge/scripts/*):

  GET /api/bridge/install.py — installer script content
  GET /api/bridge/agent.py   — agent script content (agent_claude.py + agent_core.py,
                                concatenated at request time) + config reading

Job-queue endpoint tests live in test_bridge_jobs.py.

Most tests here assert against the live served/rendered text (client.get(...))
rather than importing the module directly, since that's what actually
exercises render.py's placeholder substitution / concatenation — the exact
thing that broke repeatedly before these scripts were split out of
routers/bridge.py into real files. A few tests that only need to call a pure
function in isolation import bridge.scripts.install / bridge.scripts.agent_core
directly, which works cleanly now that both have real `if __name__ ==
"__main__":` guards (no more exec+catch-SystemExit hack).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import bridge.scripts.agent_core as agent_core
import bridge.scripts.install as install_module
from main import app
from deps import get_db


# ── In-memory DB ──────────────────────────────────────────────────────────────

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=engine)
    yield
    models.Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_install_script(client):
    """GET /api/bridge/install.py requires ?token=<bridge install token>, so tests
    that just want the script content fetch a fresh token first."""
    token = client.get("/api/bridge/install-token").json()["token"]
    return client.get(f"/api/bridge/install.py?token={token}")


# ── GET /api/bridge/install.py ────────────────────────────────────────────────

class TestInstallScript:

    def test_returns_python_text(self, client):
        res = _get_install_script(client)
        assert res.status_code == 200
        assert "python" in res.text.lower() or "import" in res.text

    def test_contains_main_function(self, client):
        res = _get_install_script(client)
        assert "def main" in res.text

    def test_contains_install_dir(self, client):
        res = _get_install_script(client)
        assert "qtask-bridge" in res.text

    def test_writes_claude_toml(self, client):
        res = _get_install_script(client)
        assert "claude.toml" in res.text

    def test_claude_toml_only_written_if_not_exists(self, client):
        res = _get_install_script(client)
        assert "already exists" in res.text  # skips on reinstall

    def test_toml_template_documents_setup_cmd(self, client):
        res = _get_install_script(client)
        assert "setup_cmd" in res.text

    def test_toml_template_documents_per_repo_table_form(self, client):
        res = _get_install_script(client)
        assert '[repos."owner/api"]' in res.text

    def test_usage_mentions_tag_and_cleanup(self, client):
        res = _get_install_script(client)
        assert "--tag" in res.text
        assert "--cleanup" in res.text

    def test_toml_template_content_is_not_indented_at_runtime(self, client):
        """Regression guard: TOML_TEMPLATE is nested inside its own
        textwrap.dedent() specifically so the *outer* script dedent (which
        strips a shared 8-space prefix) isn't defeated by a column-0 line —
        but that only matters if the nested dedent actually resolves back to
        column 0 when the install script itself runs. Check the real,
        already-imported module's value rather than just eyeballing source."""
        assert install_module.TOML_TEMPLATE.startswith("# qtask-bridge configuration")
        for line in install_module.TOML_TEMPLATE.splitlines():
            if line.strip():
                assert not line.startswith(" "), f"indented toml line: {line!r}"

    def test_configures_global_gitignore_for_bridge_files(self, client):
        """Bridge files must be ignored via git's global core.excludesFile,
        not by editing any target repo's own .gitignore -- see
        test_does_not_touch_worktree_local_gitignore in TestAgentScript for
        the other half of this guarantee."""
        res = _get_install_script(client)
        assert "def setup_global_gitignore" in res.text
        assert "core.excludesFile" in res.text
        assert "BRIDGE_SPEC.md" in res.text
        assert ".claude/settings.local.json" in res.text
        assert ".env.qtask" in res.text
        # Called from main() for every install, not just some dead code path
        assert "setup_global_gitignore()" in res.text

    def test_global_gitignore_creates_excludes_file_when_unset(self, client, monkeypatch, tmp_path):
        """Executes the real function rather than just asserting on
        strings -- but with subprocess and the fallback path fully
        redirected into tmp_path, so this can never touch the real
        machine's actual git config or filesystem."""
        fake_excludes_path = tmp_path / "ignore_qtask_bridge"

        git_config_set_calls = []

        def fake_run(cmd, **kwargs):
            class Result:
                stdout = ""
                returncode = 0
            if cmd[:4] == ["git", "config", "--global", "--get"]:
                return Result()  # simulates core.excludesFile being unset
            if cmd[:3] == ["git", "config", "--global"]:
                git_config_set_calls.append(cmd)
                return Result()
            raise AssertionError(f"unexpected subprocess call: {cmd}")

        monkeypatch.setattr(install_module.subprocess, "run", fake_run)
        real_expanduser = os.path.expanduser
        monkeypatch.setattr(
            install_module.os.path, "expanduser",
            lambda p: str(fake_excludes_path) if "ignore_qtask_bridge" in p else real_expanduser(p),
        )

        install_module.setup_global_gitignore()

        assert git_config_set_calls == [["git", "config", "--global", "core.excludesFile", str(fake_excludes_path)]]
        assert fake_excludes_path.exists()
        content = fake_excludes_path.read_text()
        for entry in install_module.BRIDGE_IGNORE_ENTRIES:
            assert entry in content

    def test_global_gitignore_appends_to_existing_excludes_file_when_already_set(self, client, monkeypatch, tmp_path):
        """If the user already has a core.excludesFile configured, append
        to it -- never silently redirect git to a different file."""
        existing_excludes = tmp_path / "my-own-global-gitignore"
        existing_excludes.write_text("*.swp\n")

        def fake_run(cmd, **kwargs):
            class Result:
                stdout = str(existing_excludes) + "\n"
                returncode = 0
            if cmd[:4] == ["git", "config", "--global", "--get"]:
                return Result()
            raise AssertionError(f"should not reconfigure an already-set excludesFile: {cmd}")

        monkeypatch.setattr(install_module.subprocess, "run", fake_run)

        install_module.setup_global_gitignore()

        content = existing_excludes.read_text()
        assert "*.swp" in content  # untouched
        for entry in install_module.BRIDGE_IGNORE_ENTRIES:
            assert entry in content

    def test_global_gitignore_is_idempotent(self, client, monkeypatch, tmp_path):
        """Re-running the installer (e.g. to pick up a bridge update) must
        not duplicate entries on every run."""
        excludes_path = tmp_path / "ignore_qtask_bridge"
        excludes_path.write_text("\n".join(install_module.BRIDGE_IGNORE_ENTRIES) + "\n")

        def fake_run(cmd, **kwargs):
            class Result:
                stdout = str(excludes_path) + "\n"
                returncode = 0
            return Result()

        monkeypatch.setattr(install_module.subprocess, "run", fake_run)

        install_module.setup_global_gitignore()

        content = excludes_path.read_text()
        for entry in install_module.BRIDGE_IGNORE_ENTRIES:
            assert content.count(entry) == 1

    def test_missing_token_is_rejected(self, client):
        res = client.get("/api/bridge/install.py")
        assert res.status_code == 422

    def test_wrong_token_is_rejected(self, client):
        res = client.get("/api/bridge/install.py?token=not-the-real-token")
        assert res.status_code == 401

    def test_install_token_is_stable_across_requests(self, client):
        first = client.get("/api/bridge/install-token").json()["token"]
        second = client.get("/api/bridge/install-token").json()["token"]
        assert first == second

    def test_rotate_install_token_changes_it_and_invalidates_the_old_one(self, client):
        old_token = client.get("/api/bridge/install-token").json()["token"]
        new_token = client.post("/api/bridge/install-token/rotate").json()["token"]
        assert new_token != old_token
        assert client.get(f"/api/bridge/install.py?token={old_token}").status_code == 401
        assert client.get(f"/api/bridge/install.py?token={new_token}").status_code == 200

    def test_ssl_cert_error_shows_actionable_message_instead_of_raw_traceback(self, client, monkeypatch):
        """Regression test for a real failure seen in the wild: the official
        python.org macOS installer doesn't ship a CA bundle, so urlopen raises
        ssl.SSLCertVerificationError while downloading agent.py. main() should
        catch that specific error and print a fix instead of a raw traceback."""
        import ssl
        import urllib.error

        class _FailingOpener:
            def __enter__(self):
                raise urllib.error.URLError(ssl.SSLCertVerificationError("cert verify failed"))

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(install_module, "APP_URL", "https://example.com")
        monkeypatch.setattr(install_module.urllib.request, "urlopen", lambda req: _FailingOpener())

        printed = []
        monkeypatch.setattr(install_module, "print", lambda *a, **k: printed.append(" ".join(str(x) for x in a)), raising=False)

        with pytest.raises(SystemExit) as exc_info:
            install_module.main()
        assert exc_info.value.code == 1

        output = "\n".join(printed)
        assert "Install Certificates.command" in output
        assert "Traceback" not in output

    def test_other_url_errors_still_propagate(self, client, monkeypatch):
        """Only the specific SSL-cert failure gets a friendly message — any
        other download failure (network down, DNS, etc.) should still raise
        normally rather than being silently swallowed."""
        import urllib.error

        def _raise_generic(req):
            raise urllib.error.URLError("network is unreachable")

        monkeypatch.setattr(install_module, "APP_URL", "https://example.com")
        monkeypatch.setattr(install_module.urllib.request, "urlopen", _raise_generic)
        monkeypatch.setattr(install_module, "print", lambda *a, **k: None, raising=False)

        with pytest.raises(urllib.error.URLError):
            install_module.main()


# ── Served scripts compile as valid Python ───────────────────────────────────

class TestServedScriptsCompile:
    """Both scripts are real Python files concatenated/rendered at request
    time -- this still compiles the actual served text for real on every
    test run, since a bug in render.py's concatenation (or a syntax error
    in either source file) would only show up there, not in a plain
    `import bridge.scripts.agent_core`."""

    def test_agent_script_compiles(self, client):
        res = client.get("/api/bridge/agent.py")
        compile(res.text, "agent.py", "exec")

    def test_install_script_compiles(self, client):
        res = _get_install_script(client)
        compile(res.text, "install.py", "exec")

    def test_agent_script_top_level_statements_are_not_indented(self, client):
        """Regression guard for the same class of dedent bug as install.py:
        the first real statement (the module docstring) must start at column 0."""
        res = client.get("/api/bridge/agent.py")
        lines = res.text.splitlines()
        first_code_line = next(l for l in lines if l.strip() and not l.startswith("#!"))
        assert not first_code_line.startswith(" "), repr(first_code_line)

    def test_agent_script_shebang_is_the_literal_first_line(self, client):
        """Regression test: render_agent_script() concatenates agent_core.py
        (which owns the #!/usr/bin/env python3 shebang) and agent_claude.py.
        Getting that order backwards once put the shebang partway through
        the file instead of on line 1 -- harmless when `python3 agent.py`
        is run explicitly, but the installed ~/.local/bin/qtask-bridge is
        chmod +x'd and invoked directly, which relies on line 1 being a
        valid interpreter directive. Without it the shell tries to execute
        the file as its own script and fails with garbage like "import:
        command not found" -- this exact bug reached a real second machine
        before being caught, since every other check here (compiles,
        contains expected functions, top-level statements not indented)
        stayed green regardless of concatenation order."""
        res = client.get("/api/bridge/agent.py")
        assert res.text.splitlines()[0] == "#!/usr/bin/env python3"

    def test_agent_script_actually_executes_via_its_shebang(self, client, tmp_path, monkeypatch):
        """Stronger version of the above: don't just check the string, run
        the served file for real exactly the way the installed binary is
        invoked (direct execution relying on the shebang, not `python3
        <path>`) and confirm it behaves like a real Python script instead
        of being swallowed by the shell. HOME is redirected into tmp_path
        so this can't pick up a real ~/.config/qtask-bridge/config.json on
        the machine running the test and behave unpredictably."""
        res = client.get("/api/bridge/agent.py")
        script_path = tmp_path / "qtask-bridge"
        script_path.write_text(res.text)
        script_path.chmod(0o755)

        env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}
        result = subprocess.run(
            [str(script_path), "--list"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        # A real Python run (even one that errors out because no config
        # exists in this isolated HOME) fails cleanly with our own message
        # -- a shell trying to execute the file as its own script instead
        # produces "command not found" / "syntax error" noise.
        assert "command not found" not in result.stderr
        assert "syntax error" not in result.stderr
        assert "Config not found" in result.stdout or "Config not found" in result.stderr


class TestBridgeScriptsExemptFromAuth:
    """Only install.py is exempt from AuthMiddleware's session-cookie/bearer-token
    check — that's the entire point of a "pre-authed" install script: a machine
    with no prior credentials must be able to `curl` it. It's gated instead by
    its own scoped, rotatable install token (see TestInstallScript), so exemption
    from the *global* check doesn't mean exemption from all checks.

    agent.py is NOT exempt: the install script always fetches it with a real
    `Authorization: Bearer <AUTH_PASSWORD>` header (see install.py's main()),
    so it never needs a bare, credential-free fetch path.

    The other bridge tests all run with AUTH_PASSWORD unset, which makes
    AuthMiddleware a no-op, so they can't catch a regression here. These tests
    turn auth on for real."""

    @pytest.fixture
    def auth_client(self, monkeypatch):
        monkeypatch.setattr("main.AUTH_PASSWORD", "s3cret")
        monkeypatch.setattr("main.SESSION_TOKEN", "unrelated-session-token")
        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()

    def test_install_script_reachable_with_its_own_token_and_no_other_credentials(self, auth_client):
        token_res = auth_client.get(
            "/api/bridge/install-token", headers={"Authorization": "Bearer s3cret"}
        )
        assert token_res.status_code == 200
        install_token = token_res.json()["token"]

        res = auth_client.get(f"/api/bridge/install.py?token={install_token}")
        assert res.status_code == 200

    def test_install_script_still_rejects_wrong_token_when_auth_password_set(self, auth_client):
        res = auth_client.get("/api/bridge/install.py?token=wrong")
        assert res.status_code == 401

    def test_agent_script_requires_real_credentials(self, auth_client):
        res = auth_client.get("/api/bridge/agent.py")
        assert res.status_code == 401

    def test_agent_script_reachable_with_bearer_token(self, auth_client):
        res = auth_client.get(
            "/api/bridge/agent.py", headers={"Authorization": "Bearer s3cret"}
        )
        assert res.status_code == 200

    def test_other_bridge_routes_still_require_credentials(self, auth_client):
        """Sanity check that AuthMiddleware is actually active in this test
        and the install.py exemption above isn't just masking a broken middleware."""
        res = auth_client.get("/api/bridge/jobs/card/1/latest")
        assert res.status_code == 401


# ── GET /api/bridge/agent.py ──────────────────────────────────────────────────

class TestAgentScript:

    def test_returns_python_text(self, client):
        res = client.get("/api/bridge/agent.py")
        assert res.status_code == 200
        assert "import" in res.text

    def test_contains_watch_and_card_modes(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "--watch" in res.text
        assert "--card" in res.text

    def test_launch_command_does_not_use_bad_flag(self, client):
        """Verify the agent no longer uses the non-functional --print-path-to-claude-code-settings flag."""
        res = client.get("/api/bridge/agent.py")
        assert "--print-path-to-claude-code-settings" not in res.text

    def test_launch_command_invokes_claude_with_prompt(self, client):
        res = client.get("/api/bridge/agent.py")
        assert '"claude"' in res.text or "'claude'" in res.text
        assert "BRIDGE_SPEC" in res.text

    def test_agent_reads_claude_toml(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "claude.toml" in res.text
        assert "tomllib" in res.text

    def test_agent_has_resolve_work_dir(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "_resolve_work_dir" in res.text

    def test_agent_has_repo_from_git_url(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "_repo_from_git_url" in res.text

    def test_agent_passes_repos_filter_to_api(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "repos=" in res.text

    def test_contains_tag_and_cleanup_modes(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "--tag" in res.text
        assert "--cleanup" in res.text
        assert "def cmd_tag" in res.text
        assert "def cmd_cleanup" in res.text

    def test_tag_mode_queues_via_tag_endpoint(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "/api/bridge/jobs/queue-by-tag" in res.text

    def test_uses_git_worktree_instead_of_in_place_checkout(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "git worktree add" in res.text
        assert "def _create_worktree" in res.text
        # The old in-place-checkout function is gone, not just renamed-and-kept
        assert "def _git_setup" not in res.text

    def test_worktree_branches_off_fetched_remote_not_local_checkout(self, client):
        """Isolation only works if the worktree is created off origin/<primary>
        without ever checking out the primary branch in the base repo."""
        res = client.get("/api/bridge/agent.py")
        assert '"git", "fetch", "origin"' in res.text
        assert 'f"origin/{primary}"' in res.text

    def test_no_longer_aborts_on_uncommitted_changes_in_base_repo(self, client):
        """That check only made sense when the bridge checked out branches
        in-place; worktrees make it obsolete."""
        res = client.get("/api/bridge/agent.py")
        assert "Uncommitted changes detected" not in res.text

    def test_claude_launch_passes_worktree_cwd(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "def _run_interactive(cfg, job_id, branch, cwd" in res.text
        assert "def _run_streaming(cfg, job_id, branch, cwd" in res.text

    def test_supports_setup_cmd(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "def _run_setup_cmd" in res.text
        assert "setup_cmd" in res.text

    def test_repo_entry_supports_table_form_with_setup_cmd(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "def _repo_entry" in res.text

    def test_cleanup_only_targets_qtask_branches(self, client):
        """--cleanup should never touch a worktree for a branch the user made
        themselves — only branches under the qtask/ prefix."""
        res = client.get("/api/bridge/agent.py")
        assert "refs/heads/qtask/" in res.text

    def test_cleanup_checks_merge_status(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "def _is_branch_merged" in res.text
        assert "--is-ancestor" in res.text

    def test_sends_worktree_path_on_start(self, client):
        res = client.get("/api/bridge/agent.py")
        assert '"worktree_path": worktree_path' in res.text

    def test_writes_last_worktree_pointer_file(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "LAST_WORKTREE_FILE" in res.text
        assert "last-worktree" in res.text

    def test_writes_ide_settings_via_adapter(self, client):
        """IDE-settings writing is Claude-specific (agent_claude.py's
        write_ide_settings) -- agent_core.py only calls it by name, so the
        served text still contains it after concatenation."""
        res = client.get("/api/bridge/agent.py")
        assert "def write_ide_settings" in res.text
        assert "statusLine" in res.text
        assert ".claude" in res.text
        assert "settings.local.json" in res.text
        # Called from run_job for every job, not just some code path that's dead
        assert "write_ide_settings(worktree_path)" in res.text

    def test_does_not_touch_worktree_local_gitignore(self, client):
        """Ignoring bridge files is handled globally at install time (see
        TestInstallScript) -- agent.py must never write to a target repo's
        own .gitignore."""
        res = client.get("/api/bridge/agent.py")
        assert "def ensure_gitignore" not in res.text
        assert "GITIGNORE_ENTRIES" not in res.text

    def test_writes_reserved_port_range_and_db_name(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "def _write_qtask_env" in res.text
        assert "QTASK_PORT_RANGE" in res.text
        assert "QTASK_DB_NAME" in res.text
        # Called from run_job for every job, not just some code path that's dead
        assert "_write_qtask_env(worktree_path, job_id)" in res.text

    def test_prompt_points_claude_at_the_reserved_env_file(self, client):
        """Otherwise the file is just sitting there and the agent has to
        stumble onto it -- the whole point is it doesn't have to."""
        res = client.get("/api/bridge/agent.py")
        assert "def _make_prompt" in res.text
        prompt_start = res.text.index("def _make_prompt")
        prompt_end = res.text.index("def _detect_primary_branch")
        # {ENV_FILENAME}, not the literal ".env.qtask" -- this is the served
        # SOURCE text, substitution only happens when the downloaded script runs.
        assert "{ENV_FILENAME}" in res.text[prompt_start:prompt_end]

    def test_env_content_actually_produces_valid_shell_syntax(self, tmp_path):
        """Execute the real function against a real directory rather than
        just asserting on strings -- confirms the file is genuinely
        sourceable shell syntax, not just text that looks plausible."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        agent_core._write_qtask_env(str(worktree), 77)

        env_path = worktree / ".env.qtask"
        assert env_path.exists()
        content = env_path.read_text()

        assert "QTASK_JOB_ID=77" in content
        assert "QTASK_PORT_BASE=20770" in content
        assert "QTASK_PORT_RANGE=20770-20779" in content
        assert "QTASK_DB_NAME=qtask_job_77" in content

        # A real shell must be able to source it without error, and every
        # assigned value must actually be usable afterward.
        result = subprocess.run(
            ["sh", "-c", f"set -a; . {env_path}; set +a; "
             "echo \"$QTASK_JOB_ID|$QTASK_PORT_BASE|$QTASK_PORT_RANGE|$QTASK_DB_NAME\""],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "77|20770|20770-20779|qtask_job_77"

    def test_port_range_derivation_is_deterministic_and_ten_wide(self, tmp_path):
        for job_id in (1, 400, 401, 799, 800):
            worktree = tmp_path / f"wt-{job_id}"
            worktree.mkdir()
            agent_core._write_qtask_env(str(worktree), job_id)
            content = (worktree / ".env.qtask").read_text()
            expected_base = 20000 + (job_id % 400) * 10
            assert f"QTASK_PORT_BASE={expected_base}" in content
            assert f"QTASK_PORT_RANGE={expected_base}-{expected_base + 9}" in content
        # job 1 and job 401 land in the same bucket (401 % 400 == 1) -- that's
        # the documented wraparound, not a bug, as long as it only matters
        # for hundreds of concurrently-uncleaned worktrees.
        wt1 = (tmp_path / "wt-1" / ".env.qtask").read_text()
        wt401 = (tmp_path / "wt-401" / ".env.qtask").read_text()
        assert "QTASK_PORT_BASE=20010" in wt1
        assert "QTASK_PORT_BASE=20010" in wt401

    def test_sets_terminal_title_for_interactive_sessions_only(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "def _set_terminal_title" in res.text
        # OSC escape sequence for setting the terminal tab/window title
        assert "\\033]0;" in res.text
        # Called from the interactive path...
        assert "_set_terminal_title(branch)" in res.text
        # ...but _run_streaming (unattended --tag/--watch) never calls it
        streaming_start = res.text.index("def _run_streaming")
        streaming_end = res.text.index("def run_job")
        assert "_set_terminal_title" not in res.text[streaming_start:streaming_end]

    def test_list_command_is_read_only(self, client):
        """--list must never prompt for removal like --cleanup does -- it's
        meant to be safe to run from a script or muscle memory."""
        res = client.get("/api/bridge/agent.py")
        assert "--list" in res.text
        assert "def cmd_list" in res.text
        list_start = res.text.index("def cmd_list")
        list_end = res.text.index("def cmd_cleanup")
        list_body = res.text[list_start:list_end]
        assert "input(" not in list_body
        assert "git worktree remove" not in list_body

    def test_list_and_cleanup_share_the_same_scan(self, client):
        """Both must find the exact same worktrees, or --list would show
        something --cleanup can't act on (or vice versa)."""
        res = client.get("/api/bridge/agent.py")
        assert "def _scan_qtask_worktrees" in res.text
        assert "_scan_qtask_worktrees(cfg)" in res.text
        # cmd_cleanup should call the shared scanner, not have its own inline loop
        cleanup_start = res.text.index("def cmd_cleanup")
        cleanup_body = res.text[cleanup_start:cleanup_start + 600]
        assert "_scan_qtask_worktrees(cfg)" in cleanup_body

    def test_heartbeat_thread_pings_while_agent_runs(self, client):
        """See bridge/stale.py -- a job stuck at 'running' forever (crashed
        agent, sleeping laptop, dropped network) is only detectable
        server-side if something pings while the process is alive, since
        interactive mode posts no output at all until the session ends."""
        res = client.get("/api/bridge/agent.py")
        assert "def _start_heartbeat" in res.text
        assert "/heartbeat" in res.text
        assert "_start_heartbeat(cfg, job_id)" in res.text

    def test_verification_runs_before_completion(self, client):
        """Verification (test_cmd + acceptance check) must run -- and the
        heartbeat must still be alive -- before the job is marked complete,
        not after; otherwise a slow check either gets lost or looks stalled."""
        res = client.get("/api/bridge/agent.py")
        assert "def _run_verification" in res.text
        assert "_run_verification(cwd, test_cmd, verify_acceptance, spec_text)" in res.text
        # Interactive: verification happens inside the heartbeat's try/finally,
        # i.e. before stop_heartbeat.set()
        interactive_start = res.text.index("def _run_interactive")
        interactive_end = res.text.index("def _run_streaming")
        interactive_body = res.text[interactive_start:interactive_end]
        assert interactive_body.index("_run_verification(") < interactive_body.index("stop_heartbeat.set()")
        # Streaming: only verifies on the success path
        streaming_start = res.text.index("def _run_streaming")
        streaming_end = res.text.index("def run_job")
        streaming_body = res.text[streaming_start:streaming_end]
        assert "if proc.returncode == 0:" in streaming_body
        assert streaming_body.index("_run_verification(") < streaming_body.index("stop_heartbeat.set()")


# ── Verification: test_cmd + acceptance-criteria check ────────────────────────

class TestExtractSection:

    def test_finds_section(self):
        spec = "## Problem Statement\nfoo\n\n## Acceptance Criteria\n- [ ] does the thing\n\n## Open Questions\nnone"
        assert agent_core._extract_section(spec, "Acceptance Criteria") == "- [ ] does the thing"

    def test_returns_none_when_heading_absent(self):
        spec = "## Problem Statement\nfoo"
        assert agent_core._extract_section(spec, "Acceptance Criteria") is None

    def test_returns_none_for_empty_spec(self):
        assert agent_core._extract_section("", "Acceptance Criteria") is None
        assert agent_core._extract_section(None, "Acceptance Criteria") is None

    def test_handles_section_at_end_of_doc(self):
        spec = "## Problem Statement\nfoo\n\n## Acceptance Criteria\n- [ ] last section, no trailing heading"
        assert agent_core._extract_section(spec, "Acceptance Criteria") == \
            "- [ ] last section, no trailing heading"

    def test_stops_at_next_heading_not_later_content(self):
        spec = "## Acceptance Criteria\n- [ ] a\n- [ ] b\n\n## Constraints & Notes\nunrelated stuff"
        result = agent_core._extract_section(spec, "Acceptance Criteria")
        assert "unrelated stuff" not in result
        assert "- [ ] a" in result and "- [ ] b" in result


class TestRunTestCmd:

    def test_passing_command_reports_passed(self, tmp_path):
        summary = agent_core._run_test_cmd(str(tmp_path), "python3 -c \"print('all good')\"")
        assert "**passed**" in summary
        assert "all good" in summary
        assert "npm test" not in summary  # sanity: doesn't hardcode a command name

    def test_failing_command_reports_failed_with_output(self, tmp_path):
        summary = agent_core._run_test_cmd(str(tmp_path), "python3 -c \"import sys; print('boom'); sys.exit(1)\"")
        assert "failed (exit 1)" in summary
        assert "boom" in summary

    def test_truncates_long_output(self, tmp_path):
        cmd = "python3 -c \"[print(i) for i in range(500)]\""
        summary = agent_core._run_test_cmd(str(tmp_path), cmd)
        body_lines = summary.split("```")[1].strip().splitlines()
        assert len(body_lines) <= agent_core.VERIFICATION_OUTPUT_MAX_LINES
        assert body_lines[-1] == "499"  # keeps the tail, not the head

    def test_includes_the_command_in_the_heading(self, tmp_path):
        summary = agent_core._run_test_cmd(str(tmp_path), "true")
        assert "`true`" in summary


class TestMakeAcceptanceCheckPrompt:

    def test_includes_criteria_text(self):
        prompt = agent_core._make_acceptance_check_prompt("- [ ] users can log in")
        assert "users can log in" in prompt

    def test_instructs_read_only(self):
        prompt = agent_core._make_acceptance_check_prompt("- [ ] x")
        assert "do not modify" in prompt.lower()

    def test_includes_test_summary_when_given(self):
        prompt = agent_core._make_acceptance_check_prompt("- [ ] x", test_summary="**passed**")
        assert "## Test Results" in prompt
        assert "**passed**" in prompt

    def test_omits_test_results_section_when_not_given(self):
        prompt = agent_core._make_acceptance_check_prompt("- [ ] x")
        assert "## Test Results" not in prompt


class TestRunVerification:

    SPEC_WITH_CRITERIA = (
        "## Problem Statement\nfix the bug\n\n"
        "## Acceptance Criteria\n- [ ] the bug is fixed\n\n"
        "## Open Questions\nnone"
    )

    def test_neither_configured_returns_empty_string(self, tmp_path):
        result = agent_core._run_verification(str(tmp_path), None, False, self.SPEC_WITH_CRITERIA)
        assert result == ""

    def test_test_cmd_only(self, tmp_path):
        result = agent_core._run_verification(
            str(tmp_path), "python3 -c \"print('ok')\"", False, self.SPEC_WITH_CRITERIA
        )
        assert result.startswith("## Verification")
        assert "### Tests" in result
        assert "### Acceptance Criteria" not in result

    def test_both_configured_produces_both_sections(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_core, "streaming_command", lambda prompt: ["echo", "MET: yes"], raising=False)
        result = agent_core._run_verification(
            str(tmp_path), "python3 -c \"print('ok')\"", True, self.SPEC_WITH_CRITERIA
        )
        assert "### Tests" in result
        assert "### Acceptance Criteria" in result
        assert "MET: yes" in result

    def test_verify_acceptance_with_no_criteria_section_skips_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_core, "streaming_command", lambda prompt: ["echo", "should not run"], raising=False)
        result = agent_core._run_verification(
            str(tmp_path), None, True, "## Problem Statement\nno acceptance criteria here"
        )
        assert result == ""

    def test_acceptance_check_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_core, "streaming_command", lambda prompt: ["echo", "NOT MET: needs work"], raising=False)
        result = agent_core._run_verification(str(tmp_path), None, True, self.SPEC_WITH_CRITERIA)
        assert "### Tests" not in result
        assert "NOT MET: needs work" in result


class TestRepoEntryVerificationFields:

    def test_resolves_test_cmd_and_verify_acceptance_from_table_form(self):
        cfg = {"repos": {"owner/repo": {
            "path": "/x", "setup_cmd": "npm install",
            "test_cmd": "npm test", "verify_acceptance": True,
        }}}
        path, setup_cmd, test_cmd, verify_acceptance = agent_core._repo_entry(cfg, "owner/repo")
        assert path == "/x"
        assert setup_cmd == "npm install"
        assert test_cmd == "npm test"
        assert verify_acceptance is True

    def test_plain_string_form_returns_none_for_new_fields(self):
        cfg = {"repos": {"owner/repo": "/x"}}
        path, setup_cmd, test_cmd, verify_acceptance = agent_core._repo_entry(cfg, "owner/repo")
        assert path == "/x"
        assert test_cmd is None
        assert verify_acceptance is None

    def test_unconfigured_repo_returns_all_none(self):
        assert agent_core._repo_entry({"repos": {}}, "owner/repo") == (None, None, None, None)
