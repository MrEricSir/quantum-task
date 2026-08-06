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
directly, which works cleanly since install.py has a real `if __name__ ==
"__main__":` guard (no exec+catch-SystemExit hack needed). agent_core.py
does NOT have its own guard -- see bridge/render.py's render_agent_script()
and agent_core.py's module docstring for why: that guard has to be appended
once, after both concatenated files, or main() can fire before the adapter
file's definitions (interactive_command, write_ide_settings, etc.) have
executed. Got this wrong once already (shipped a real NameError to a live
machine) -- TestAgentScriptFullFlow below exercises run_job() through the
actual rendered/concatenated text specifically to catch a regression here,
not just import bridge.scripts.agent_core in isolation.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import http.server
import json
import subprocess
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import bridge.render as bridge_render
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


class _FakeBridgeBackend:
    """A real local HTTP server implementing just enough of /api/bridge/*
    for a --card run to complete, so the rendered agent.py can be driven
    as a genuine subprocess making real urllib.request calls -- not
    monkeypatched -- the same way the actual installed binary talks to the
    real app. Deliberately minimal: enough to exercise create -> claim ->
    start -> (heartbeat, if it ever fires) -> complete/error/output."""

    def __init__(self, job):
        self.job = dict(job)
        self.calls = []
        backend = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass  # keep test output quiet

            def _body(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                return json.loads(self.rfile.read(length)) if length else None

            def _respond(self, obj, status=200):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = self._body()
                backend.calls.append(("POST", self.path, body))
                if self.path == "/api/bridge/jobs":
                    self._respond({"id": backend.job["id"]})
                elif self.path.endswith("/start"):
                    backend.job.update(body or {})
                    self._respond({"ok": True})
                else:
                    self._respond({"ok": True})

            def do_GET(self):
                backend.calls.append(("GET", self.path, None))
                if self.path.startswith("/api/bridge/jobs/next/pending"):
                    self._respond({"job": backend.job})
                else:
                    self._respond({})

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def calls_to(self, path_suffix):
        return [c for c in self.calls if c[1].endswith(path_suffix)]


@pytest.fixture
def scratch_repo(tmp_path):
    """A real, throwaway git repo (bare remote + clone) for tests that
    need to exercise actual git commands rather than mock them. Shared
    across test classes in this file."""
    remote = tmp_path / "remote"
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=clone, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=clone, check=True)
    (clone / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "README.md"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=clone, check=True)
    return clone


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


# ── Regression: run_job() through the actual rendered/concatenated text ───────

class TestAgentScriptFullFlow:
    """Regression coverage for the "write_ide_settings not defined" bug: the
    served agent.py's `if __name__ == "__main__": main()` guard fires
    IMMEDIATELY, synchronously, the moment exec() reaches it -- unlike an
    ordinary function call, which only resolves names when actually
    invoked. If that guard sits inside agent_core.py's own source (which is
    textually first, so its shebang lands on line 1), main() fires before
    agent_claude.py's definitions -- sitting textually after the guard --
    have ever executed, and the first adapter name main()'s call graph
    touches (write_ide_settings, in run_job) raises NameError. Every other
    check in this file (compiles, contains the right functions, isn't
    mis-indented, shebang is line 1) stayed green through this exact bug,
    because none of them actually run the script as __main__ far enough to
    reach an adapter-supplied name -- this shipped to a real second machine
    before being caught."""

    def test_adapter_names_are_defined_before_the_main_guard(self, client):
        """The direct, structural version of the regression check: whatever
        the concatenation order, every name agent_core.py's own module
        docstring lists as the adapter contract must appear in the source
        BEFORE the (single, appended-by-render.py) __main__ guard -- or
        whichever of them main()'s call graph reaches first raises
        NameError the moment a real run reaches it, exactly as it did here."""
        res = client.get("/api/bridge/agent.py")
        lines = res.text.splitlines()
        guard_line_nums = [i for i, l in enumerate(lines) if l.strip() == 'if __name__ == "__main__":']
        assert len(guard_line_nums) == 1, \
            f"expected exactly one top-level __main__ guard line, found {len(guard_line_nums)}"
        guard_line = guard_line_nums[0]
        for name, marker in [
            ("AGENT_LABEL", "AGENT_LABEL ="),
            ("AGENT_NOT_FOUND_HINT", "AGENT_NOT_FOUND_HINT ="),
            ("interactive_command", "def interactive_command"),
            ("streaming_command", "def streaming_command"),
            ("write_ide_settings", "def write_ide_settings"),
        ]:
            defined_line = next(i for i, l in enumerate(lines) if l.startswith(marker))
            assert defined_line < guard_line, \
                f"{name} is defined AFTER the __main__ guard — main() would fire before it exists"

    def _load_rendered_agent_module(self):
        """exec the real request-time-rendered agent.py -- deliberately not
        under __name__ == "__main__", so main() itself doesn't fire, but
        every top-level statement in both concatenated files (including all
        function/constant definitions) still executes, exactly as it would
        for real. This is the one place in this test file that needs the
        actual concatenation, not a plain import."""
        script_text = bridge_render.render_agent_script()
        ns = {"__name__": "test_full_flow"}
        exec(compile(script_text, "agent.py", "exec"), ns)  # noqa: S102
        return ns

    def test_run_job_reaches_write_ide_settings_without_nameerror(self, scratch_repo, monkeypatch):
        """Broader integration check: run_job()'s full flow (worktree
        creation, .env.qtask, write_ide_settings, teardown) against a real
        scratch repo. Doesn't run under __name__ == "__main__" (that would
        need a real backend for cmd_card's network calls), so on its own it
        would NOT have caught the ordering bug above -- the structural test
        is what actually pins that invariant down. This one guards against
        a different failure mode: something in the real worktree/settings
        flow breaking for reasons unrelated to concatenation order."""
        ns = self._load_rendered_agent_module()

        api_calls = []
        ns["api"] = lambda cfg, method, path, body=None: api_calls.append((method, path, body)) or {}

        # No real `claude` binary in the test environment -- stub only that
        # one subprocess call, so worktree creation / write_ide_settings /
        # teardown all still run for real against the scratch repo.
        real_run = ns["subprocess"].run
        def fake_run(cmd, *a, **k):
            if cmd[:1] == ["claude"]:
                import subprocess as _sp
                return _sp.CompletedProcess(cmd, 0)
            return real_run(cmd, *a, **k)
        monkeypatch.setattr(ns["subprocess"], "run", fake_run)

        cfg = {"app_url": "http://fake", "token": "x", "repos": {}, "repo_roots": [], "name": "test-agent"}
        job = {"id": 1, "card_id": 84, "card_title": "Fix ranking quote searches",
               "prompt": "do the thing", "target_repo": None}

        cwd_before = os.getcwd()
        os.chdir(scratch_repo)
        try:
            ns["run_job"](cfg, job, streaming=False, prompt_note=False)
        finally:
            os.chdir(cwd_before)

        settings_path = None
        for call in api_calls:
            if call[1] == "/api/bridge/jobs/1/start":
                settings_path = os.path.join(call[2]["worktree_path"], ".claude", "settings.local.json")
        assert settings_path is not None, "job never reached the /start call"
        assert os.path.exists(settings_path), "write_ide_settings never ran — NameError would land here"

        # Clean up the real worktree this test created outside tmp_path
        # (worktrees always live under ~/.local/share/qtask-bridge).
        worktree_dir = os.path.dirname(os.path.dirname(settings_path))
        subprocess.run(["git", "worktree", "remove", "--force", worktree_dir],
                       cwd=scratch_repo, capture_output=True)

    def _get_pushurl(self, repo):
        r = subprocess.run(["git", "config", "--get", "remote.origin.pushurl"],
                           cwd=repo, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def test_teardown_runs_even_if_a_pre_session_step_raises(self, scratch_repo, monkeypatch):
        """Regression test for the actual incident: remote.origin.pushurl
        stuck at PUSH_DISABLED_SENTINEL forever in a user's real repo,
        because write_ide_settings (called AFTER push is disabled but
        BEFORE the try/finally used to start) raised a real NameError on a
        live machine. Simulates any exception in that same window and
        confirms teardown still runs -- pushurl must end up unset, not
        stuck -- and that the job gets reported as errored rather than
        silently hanging at "running" forever."""
        ns = self._load_rendered_agent_module()

        api_calls = []
        ns["api"] = lambda cfg, method, path, body=None: api_calls.append((method, path, body)) or {}

        def _boom(worktree_path):
            raise NameError("name 'write_ide_settings' is not defined")
        ns["write_ide_settings"] = _boom

        cfg = {"app_url": "http://fake", "token": "x", "repos": {}, "repo_roots": [], "name": "test-agent"}
        job = {"id": 1, "card_id": 84, "card_title": "Fix ranking quote searches",
               "prompt": "do the thing", "target_repo": None}

        assert self._get_pushurl(scratch_repo) is None  # sanity: clean repo, no pushurl yet

        cwd_before = os.getcwd()
        os.chdir(scratch_repo)
        try:
            with pytest.raises(NameError):
                ns["run_job"](cfg, job, streaming=False, prompt_note=False)
        finally:
            os.chdir(cwd_before)

        # The whole point: teardown must have run despite the exception.
        assert self._get_pushurl(scratch_repo) is None, \
            "pushurl left stuck after an exception before the coding session — this is the real bug"
        assert any(call[1] == "/api/bridge/jobs/1/error" for call in api_calls), \
            "job was never reported as errored — would sit at 'running' until the 20-min stale sweep"

        # Clean up the worktree _create_worktree made before the simulated failure.
        started = next(c for c in api_calls if c[1] == "/api/bridge/jobs/1/start")
        subprocess.run(["git", "worktree", "remove", "--force", started[2]["worktree_path"]],
                       cwd=scratch_repo, capture_output=True)

    def test_stale_push_disable_sentinel_self_heals(self, scratch_repo, monkeypatch):
        """If a previous run left pushurl stuck at PUSH_DISABLED_SENTINEL
        (e.g. from the exact incident above, before this fix existed), the
        very next run must clear it rather than perpetuating it forever —
        _create_worktree used to read the stuck value as the "original"
        pushurl and faithfully restore back to it at teardown."""
        ns = self._load_rendered_agent_module()

        # Simulate a repo already left in the broken state by a past run.
        subprocess.run(["git", "config", "remote.origin.pushurl", ns["PUSH_DISABLED_SENTINEL"]],
                       cwd=scratch_repo, check=True)
        assert self._get_pushurl(scratch_repo) == ns["PUSH_DISABLED_SENTINEL"]

        api_calls = []
        ns["api"] = lambda cfg, method, path, body=None: api_calls.append((method, path, body)) or {}
        real_run = ns["subprocess"].run
        def fake_run(cmd, *a, **k):
            if cmd[:1] == ["claude"]:
                import subprocess as _sp
                return _sp.CompletedProcess(cmd, 0)
            return real_run(cmd, *a, **k)
        monkeypatch.setattr(ns["subprocess"], "run", fake_run)

        cfg = {"app_url": "http://fake", "token": "x", "repos": {}, "repo_roots": [], "name": "test-agent"}
        job = {"id": 1, "card_id": 84, "card_title": "Fix ranking quote searches",
               "prompt": "do the thing", "target_repo": None}

        cwd_before = os.getcwd()
        os.chdir(scratch_repo)
        try:
            ns["run_job"](cfg, job, streaming=False, prompt_note=False)
        finally:
            os.chdir(cwd_before)

        assert self._get_pushurl(scratch_repo) is None, \
            "stale sentinel from a previous run was restored instead of cleared — still stuck"

        started = next(c for c in api_calls if c[1] == "/api/bridge/jobs/1/start")
        subprocess.run(["git", "worktree", "remove", "--force", started[2]["worktree_path"]],
                       cwd=scratch_repo, capture_output=True)

    def test_cleanup_deletes_the_branch_not_just_the_worktree(self, scratch_repo, monkeypatch):
        """Regression test: cmd_cleanup used to only run `git worktree
        remove`, leaving the branch behind. Since _create_worktree checks
        whether the branch exists BEFORE it ever touches worktrees, a
        leftover branch alone is enough to block every future run for that
        same card with "Branch already exists" -- even right after
        --cleanup supposedly cleaned it up. Reported directly by the user."""
        ns = self._load_rendered_agent_module()
        api_calls = []
        ns["api"] = lambda cfg, method, path, body=None: api_calls.append((method, path, body)) or {}

        cfg = {"app_url": "http://fake", "token": "x",
               "repos": {"scratch/repo": str(scratch_repo)}, "repo_roots": [], "name": "test-agent"}
        job = {"id": 1, "card_id": 84, "card_title": "Fix ranking quote searches"}

        # Create a real qtask worktree + branch the same way run_job does.
        worktree_path, branch, push_url_info = ns["_create_worktree"](cfg, job, str(scratch_repo))
        ns["_git_teardown"](str(scratch_repo), push_url_info)  # restore pushurl, unrelated to this test
        assert os.path.isdir(worktree_path)
        assert self._branch_exists(scratch_repo, branch)

        # cmd_cleanup lists worktrees and prompts for a selection -- auto-pick "1".
        monkeypatch.setattr("builtins.input", lambda *_: "1")
        ns["cmd_cleanup"](cfg)

        assert not os.path.isdir(worktree_path), "worktree directory still present after cleanup"
        assert not self._branch_exists(scratch_repo, branch), \
            "branch still exists after cleanup — the next run for this card would still fail " \
            "with 'Branch already exists', which is the exact bug being fixed here"

    def _branch_exists(self, repo, branch):
        r = subprocess.run(["git", "rev-parse", "--verify", branch],
                           cwd=repo, capture_output=True)
        return r.returncode == 0

    def test_cleanup_merged_bulk_select_only_removes_merged_branches(self, scratch_repo, monkeypatch):
        """The 'merged' bulk-select path goes through the same removal loop
        as numbered picks, but was never exercised by a real test before --
        only the numbered-pick path was. A freshly created qtask branch
        with zero extra commits is trivially an ancestor of origin/main
        (same commit), so it's already "merged" with no extra setup; adding
        one local, unpushed commit makes a second branch diverge and read
        as "not merged"."""
        ns = self._load_rendered_agent_module()
        api_calls = []
        ns["api"] = lambda cfg, method, path, body=None: api_calls.append((method, path, body)) or {}
        cfg = {"app_url": "http://fake", "token": "x",
               "repos": {"scratch/repo": str(scratch_repo)}, "repo_roots": [], "name": "test-agent"}

        job_merged = {"id": 1, "card_id": 1, "card_title": "Merged card"}
        wt_merged, branch_merged, push_merged = ns["_create_worktree"](cfg, job_merged, str(scratch_repo))
        ns["_git_teardown"](str(scratch_repo), push_merged)

        job_unmerged = {"id": 2, "card_id": 2, "card_title": "Unmerged card"}
        wt_unmerged, branch_unmerged, push_unmerged = ns["_create_worktree"](cfg, job_unmerged, str(scratch_repo))
        ns["_git_teardown"](str(scratch_repo), push_unmerged)
        with open(os.path.join(wt_unmerged, "new_file.txt"), "w") as f:
            f.write("wip")
        subprocess.run(["git", "add", "new_file.txt"], cwd=wt_unmerged, check=True)
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=T",
                        "commit", "-q", "-m", "wip"], cwd=wt_unmerged, check=True)

        assert ns["_is_branch_merged"](str(scratch_repo), branch_merged) is True
        assert ns["_is_branch_merged"](str(scratch_repo), branch_unmerged) is False

        monkeypatch.setattr("builtins.input", lambda *_: "merged")
        ns["cmd_cleanup"](cfg)

        assert not os.path.isdir(wt_merged)
        assert not self._branch_exists(scratch_repo, branch_merged)
        assert os.path.isdir(wt_unmerged), "unmerged worktree removed by a 'merged'-only selection"
        assert self._branch_exists(scratch_repo, branch_unmerged), "unmerged branch removed by a 'merged'-only selection"

        subprocess.run(["git", "worktree", "remove", "--force", wt_unmerged], cwd=scratch_repo, capture_output=True)
        subprocess.run(["git", "branch", "-D", branch_unmerged], cwd=scratch_repo, capture_output=True)

    def test_cleanup_enter_to_skip_removes_nothing(self, scratch_repo, monkeypatch):
        ns = self._load_rendered_agent_module()
        api_calls = []
        ns["api"] = lambda cfg, method, path, body=None: api_calls.append((method, path, body)) or {}
        cfg = {"app_url": "http://fake", "token": "x",
               "repos": {"scratch/repo": str(scratch_repo)}, "repo_roots": [], "name": "test-agent"}
        job = {"id": 1, "card_id": 84, "card_title": "Fix ranking quote searches"}
        wt, branch, push_info = ns["_create_worktree"](cfg, job, str(scratch_repo))
        ns["_git_teardown"](str(scratch_repo), push_info)

        monkeypatch.setattr("builtins.input", lambda *_: "")
        ns["cmd_cleanup"](cfg)

        assert os.path.isdir(wt), "worktree removed despite skipping with Enter"
        assert self._branch_exists(scratch_repo, branch), "branch removed despite skipping with Enter"

        subprocess.run(["git", "worktree", "remove", "--force", wt], cwd=scratch_repo, capture_output=True)
        subprocess.run(["git", "branch", "-D", branch], cwd=scratch_repo, capture_output=True)

    def test_cleanup_multi_target_comma_separated_removes_all_selected(self, scratch_repo, monkeypatch):
        ns = self._load_rendered_agent_module()
        api_calls = []
        ns["api"] = lambda cfg, method, path, body=None: api_calls.append((method, path, body)) or {}
        cfg = {"app_url": "http://fake", "token": "x",
               "repos": {"scratch/repo": str(scratch_repo)}, "repo_roots": [], "name": "test-agent"}

        job1 = {"id": 1, "card_id": 1, "card_title": "Card one"}
        job2 = {"id": 2, "card_id": 2, "card_title": "Card two"}
        wt1, branch1, push1 = ns["_create_worktree"](cfg, job1, str(scratch_repo))
        ns["_git_teardown"](str(scratch_repo), push1)
        wt2, branch2, push2 = ns["_create_worktree"](cfg, job2, str(scratch_repo))
        ns["_git_teardown"](str(scratch_repo), push2)

        monkeypatch.setattr("builtins.input", lambda *_: "1,2")
        ns["cmd_cleanup"](cfg)

        assert not os.path.isdir(wt1) and not self._branch_exists(scratch_repo, branch1)
        assert not os.path.isdir(wt2) and not self._branch_exists(scratch_repo, branch2)

    def test_full_lifecycle_create_list_cleanup_leaves_clean_state(self, scratch_repo, monkeypatch, capsys):
        """Happy-path smoke test across the whole create -> list -> cleanup
        round trip: worktree creation (push disabled), write_ide_settings,
        .env.qtask, appearing in --list, --cleanup removing both the
        worktree and the branch, and the repo ending up in EXACTLY the
        state it started in. Empirically confirmed (by reverting each fix
        independently and re-running just this test) what it does and
        doesn't catch: it DOES catch incident 4 (--cleanup not deleting
        branches) concretely. It does NOT catch incidents 1-2 (shebang
        position, __main__ guard ordering -- this runs under __name__ !=
        "__main__", see _load_rendered_agent_module, so a misplaced guard
        never fires here regardless of source position) or incident 3
        (stuck-pushurl self-perpetuation -- only manifests when a prior run
        left the repo already broken, or when something raises between
        push-disable and teardown; this test's happy path does neither).
        Those three have their own dedicated, failure-injecting tests.
        This one's real value is as a general regression net for the round
        trip as a whole, for whatever the NEXT bug turns out to be, not as
        a re-run of today's specific incidents."""
        ns = self._load_rendered_agent_module()
        api_calls = []
        ns["api"] = lambda cfg, method, path, body=None: api_calls.append((method, path, body)) or {}
        cfg = {"app_url": "http://fake", "token": "x",
               "repos": {"scratch/repo": str(scratch_repo)}, "repo_roots": [], "name": "test-agent"}
        job = {"id": 1, "card_id": 84, "card_title": "Fix ranking quote searches",
               "prompt": "do the thing", "target_repo": None}

        assert self._get_pushurl(scratch_repo) is None  # clean starting state

        real_run = ns["subprocess"].run
        def fake_run(cmd, *a, **k):
            if cmd[:1] == ["claude"]:
                import subprocess as _sp
                return _sp.CompletedProcess(cmd, 0)
            return real_run(cmd, *a, **k)
        monkeypatch.setattr(ns["subprocess"], "run", fake_run)

        cwd_before = os.getcwd()
        os.chdir(scratch_repo)
        try:
            ns["run_job"](cfg, job, streaming=False, prompt_note=False)
        finally:
            os.chdir(cwd_before)

        started = next(c for c in api_calls if c[1] == "/api/bridge/jobs/1/start")
        worktree_path = started[2]["worktree_path"]
        branch = started[2]["branch"]

        assert os.path.isdir(worktree_path)
        assert self._branch_exists(scratch_repo, branch)
        assert os.path.exists(os.path.join(worktree_path, ".claude", "settings.local.json"))
        assert os.path.exists(os.path.join(worktree_path, ".env.qtask"))
        assert self._get_pushurl(scratch_repo) is None  # restored after the session

        capsys.readouterr()
        ns["cmd_list"](cfg)
        list_output = capsys.readouterr().out
        assert branch in list_output
        assert worktree_path in list_output

        monkeypatch.setattr("builtins.input", lambda *_: "1")
        ns["cmd_cleanup"](cfg)

        assert not os.path.isdir(worktree_path)
        assert not self._branch_exists(scratch_repo, branch)
        assert self._get_pushurl(scratch_repo) is None

        capsys.readouterr()
        ns["cmd_list"](cfg)
        assert "No qtask worktrees found" in capsys.readouterr().out


# ── The real thing: rendered agent.py run as a genuine installed binary ───────

class TestRealInstalledBinary:
    """The strongest regression guard in this file. Every other test here
    either hits the served text over HTTP (checks content), execs the
    concatenated script under a non-"__main__" namespace (sidesteps
    entrypoint semantics entirely, see TestAgentScriptFullFlow), or
    imports agent_core.py directly (never sees concatenation at all). This
    class does none of that: it writes the rendered agent.py to disk,
    chmod +x's it, and invokes it as a genuine subprocess relying on its
    own shebang -- byte-for-byte how ~/.local/bin/qtask-bridge actually
    gets run on a real machine, against a real local HTTP backend and a
    real (stub) `claude` executable on PATH, not monkeypatched internals.
    It's the only test in this file that would have caught incidents 1 and
    2 (shebang position, __main__ guard ordering) directly, rather than
    via the structural proxy check in TestAgentScriptFullFlow."""

    def test_card_flow_runs_as_a_genuine_installed_binary(self, scratch_repo, tmp_path):
        home_dir = tmp_path / "home"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (home_dir / ".config" / "qtask-bridge").mkdir(parents=True)

        # A real executable named `claude` on PATH -- not a mock -- so the
        # installed script's subprocess.run(["claude", ...]) call succeeds
        # exactly like it would with the real CLI installed.
        claude_stub = bin_dir / "claude"
        claude_stub.write_text("#!/bin/sh\nexit 0\n")
        claude_stub.chmod(0o755)

        # The rendered script, installed and chmod +x'd exactly like the
        # real installer does to ~/.local/bin/qtask-bridge.
        script_path = bin_dir / "qtask-bridge"
        script_path.write_text(bridge_render.render_agent_script())
        script_path.chmod(0o755)

        job = {
            "id": 1, "card_id": 84, "status": "pending", "target_repo": None,
            "branch_name": None, "agent_name": None, "worktree_path": None,
            "result": None, "output": None,
            "spec_snapshot": "## Acceptance Criteria\n- [ ] the fix works",
            "created_at": "2026-01-01T00:00:00", "updated_at": None,
            "card_title": "Fix ranking quote searches",
            "prompt": "Implement the fix described in BRIDGE_SPEC.md.",
            "spec": "## Acceptance Criteria\n- [ ] the fix works",
        }

        with _FakeBridgeBackend(job) as backend:
            (home_dir / ".config" / "qtask-bridge" / "config.json").write_text(
                json.dumps({"app_url": backend.url, "token": "test-token"})
            )

            env = {"HOME": str(home_dir), "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
            result = subprocess.run(
                [str(script_path), "--card", "84"],
                cwd=scratch_repo, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )

            # The exact failure signature of incidents 1 and 2, if either
            # ever regressed: a shell trying to interpret the file itself
            # (no valid shebang), or a NameError for an adapter-supplied
            # name (main() fired before the adapter's definitions existed).
            assert "command not found" not in result.stderr, result.stderr
            assert "syntax error" not in result.stderr, result.stderr
            assert "NameError" not in result.stderr, result.stderr
            assert "Traceback" not in result.stderr, result.stderr
            assert result.returncode == 0, \
                f"qtask-bridge exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

            start_calls = backend.calls_to("/start")
            assert start_calls, "job never reached POST .../start — worktree creation didn't happen"
            worktree_path = start_calls[0][2]["worktree_path"]
            branch = start_calls[0][2]["branch"]

            assert os.path.isdir(worktree_path)
            assert os.path.exists(os.path.join(worktree_path, ".claude", "settings.local.json")), \
                "write_ide_settings never ran — this is exactly the NameError incident's failure mode"
            assert os.path.exists(os.path.join(worktree_path, ".env.qtask"))

            assert backend.calls_to("/complete"), "job never reached POST .../complete"
            assert not backend.calls_to("/error"), f"job errored: {backend.calls_to('/error')}"

        # Base repo left in a clean state -- pushurl restored, not stuck.
        pushurl = subprocess.run(["git", "config", "--get", "remote.origin.pushurl"],
                                 cwd=scratch_repo, capture_output=True, text=True)
        assert pushurl.returncode != 0, "pushurl left stuck after a real end-to-end run"

        subprocess.run(["git", "worktree", "remove", "--force", worktree_path],
                       cwd=scratch_repo, capture_output=True)
        subprocess.run(["git", "branch", "-D", branch], cwd=scratch_repo, capture_output=True)
