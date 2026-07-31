"""
Tests for the bridge job queue endpoints (routers/bridge.py).

Covers:
  - POST /api/bridge/jobs              — queue job (no spec → 400, valid → 200)
  - GET  /api/bridge/jobs/{id}         — get status
  - GET  /api/bridge/jobs/next/pending — atomic claim, lazy prompt build, double-claim,
                                         ?repos= filtering
  - POST /api/bridge/jobs/{id}/start   — record branch + agent name
  - POST /api/bridge/jobs/{id}/output  — stdout chunking + line-cap truncation
  - POST /api/bridge/jobs/{id}/complete
  - POST /api/bridge/jobs/{id}/error
  - GET  /api/bridge/jobs/card/{id}/latest
  - GET  /api/bridge/install.py        — installer script content
  - GET  /api/bridge/agent.py          — agent script content + config reading
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
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

def _make_card(title="Test card", spec=None, external_id=None, description=None,
                completed=False, archived=False, tags=()):
    with TestSession() as db:
        card = models.Card(
            title=title, section="today", position=0,
            spec=spec, external_id=external_id, description=description,
            completed=completed, archived=archived,
        )
        if tags:
            card.tags = db.query(models.Tag).filter(models.Tag.name.in_(tags)).all()
        db.add(card)
        db.commit()
        db.refresh(card)
        return card.id


def _make_tag(name, color="#6b7280"):
    with TestSession() as db:
        tag = models.Tag(name=name, color=color)
        db.add(tag)
        db.commit()
        db.refresh(tag)
        return tag.id


def _make_eng_item(external_id, title="Issue", body="Issue body", number=1, repo="owner/repo"):
    with TestSession() as db:
        item = models.EngineeringItem(
            external_id=external_id,
            title=title,
            item_type="issue",
            repo=repo,
            number=number,
            url=f"https://github.com/{repo}/issues/{number}",
            state="open",
            body=body,
            synced_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.id


# ── POST /api/bridge/jobs ─────────────────────────────────────────────────────

class TestCreateBridgeJob:

    def test_404_when_card_not_found(self, client):
        res = client.post("/api/bridge/jobs", json={"card_id": 9999})
        assert res.status_code == 404

    def test_400_when_card_has_no_spec(self, client):
        card_id = _make_card("No spec card", spec=None)
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 400
        assert "spec" in res.json()["detail"].lower()

    def test_creates_pending_job_with_spec(self, client):
        card_id = _make_card("My feature", spec="## Problem\nFix the thing")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "pending"
        assert data["card_id"] == card_id
        assert data["id"] is not None

    def test_prompt_snapshot_includes_spec(self, client):
        card_id = _make_card("Auth feature", spec="## Problem\nAdd OAuth")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 200
        with TestSession() as db:
            job = db.query(models.BridgeJob).filter_by(id=res.json()["id"]).first()
            assert job.prompt_snapshot is not None
            assert "Auth feature" in job.prompt_snapshot
            assert "Add OAuth" in job.prompt_snapshot

    def test_prompt_snapshot_includes_github_context(self, client):
        ext_id = "github:owner/repo/issues/1"
        card_id = _make_card("GH feature", spec="## Fix\nDo thing", external_id=ext_id)
        _make_eng_item(ext_id, body="Issue body text")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 200
        with TestSession() as db:
            job = db.query(models.BridgeJob).filter_by(id=res.json()["id"]).first()
            assert "Issue body text" in (job.prompt_snapshot or "")

    def test_result_field_is_none_initially(self, client):
        card_id = _make_card(spec="spec text")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.json()["result"] is None

    def test_target_repo_derived_from_github_external_id(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/myapp/issues/42")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 200
        assert res.json()["target_repo"] == "owner/myapp"

    def test_target_repo_is_null_for_card_without_external_id(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.json()["target_repo"] is None

    def test_target_repo_is_null_for_non_github_external_id(self, client):
        card_id = _make_card(spec="s", external_id="jira:PROJ-123")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.json()["target_repo"] is None


# ── POST /api/bridge/jobs/queue-by-tag ────────────────────────────────────────

class TestQueueJobsByTag:

    def test_404_when_tag_not_found(self, client):
        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "nope"})
        assert res.status_code == 404

    def test_queues_jobs_for_tagged_cards_with_spec(self, client):
        _make_tag("work")
        _make_card("Fix login", spec="## Fix\nDo it", tags=("work",))
        _make_card("Fix logout", spec="## Fix\nDo it too", tags=("work",))

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert res.status_code == 200
        data = res.json()
        assert len(data["queued"]) == 2
        titles = {j["card_id"] for j in data["queued"]}
        assert len(titles) == 2
        for job in data["queued"]:
            assert job["status"] == "pending"

    def test_ignores_cards_without_the_tag(self, client):
        _make_tag("work")
        _make_tag("personal")
        _make_card("Work task", spec="s", tags=("work",))
        _make_card("Personal task", spec="s", tags=("personal",))

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert len(res.json()["queued"]) == 1

    def test_skips_cards_without_a_spec(self, client):
        _make_tag("work")
        _make_card("No spec yet", spec=None, tags=("work",))

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        data = res.json()
        assert data["queued"] == []
        assert len(data["skipped_no_spec"]) == 1
        assert data["skipped_no_spec"][0]["title"] == "No spec yet"

    def test_skips_cards_with_an_existing_pending_job(self, client):
        _make_tag("work")
        card_id = _make_card("Already queued", spec="s", tags=("work",))
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        data = res.json()
        assert data["queued"] == []
        assert len(data["skipped_already_queued"]) == 1
        assert data["skipped_already_queued"][0]["id"] == card_id

    def test_skips_cards_with_an_existing_running_job(self, client):
        _make_tag("work")
        card_id = _make_card("Already running", spec="s", tags=("work",))
        client.post("/api/bridge/jobs", json={"card_id": card_id})
        client.get("/api/bridge/jobs/next/pending")  # claims → running

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert res.json()["skipped_already_queued"] != []

    def test_requeues_card_whose_last_job_is_done(self, client):
        """A card whose previous job finished (done/error) is eligible again —
        only pending/running jobs block re-queueing."""
        _make_tag("work")
        card_id = _make_card("Redo this", spec="s", tags=("work",))
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.get("/api/bridge/jobs/next/pending")
        client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "done"})

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert len(res.json()["queued"]) == 1

    def test_skips_completed_cards(self, client):
        _make_tag("work")
        _make_card("Done already", spec="s", tags=("work",), completed=True)

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        data = res.json()
        assert data["queued"] == []
        assert data["skipped_no_spec"] == []
        assert data["skipped_already_queued"] == []

    def test_skips_archived_cards(self, client):
        _make_tag("work")
        _make_card("Archived", spec="s", tags=("work",), archived=True)

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert res.json()["queued"] == []

    def test_tag_match_is_case_insensitive(self, client):
        _make_tag("Work")
        _make_card("Fix login", spec="s", tags=("Work",))

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert len(res.json()["queued"]) == 1

    def test_queued_job_has_correct_target_repo_and_spec(self, client):
        _make_tag("work")
        _make_card("GH card", spec="## Do the thing", tags=("work",),
                    external_id="github:owner/myapp/issues/9")

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        job = res.json()["queued"][0]
        assert job["target_repo"] == "owner/myapp"
        assert job["spec_snapshot"] == "## Do the thing"


# ── GET /api/bridge/jobs/{id} ─────────────────────────────────────────────────

class TestGetBridgeJob:

    def test_404_for_missing_job(self, client):
        res = client.get("/api/bridge/jobs/9999")
        assert res.status_code == 404

    def test_returns_correct_shape(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        job_id = res.json()["id"]

        res2 = client.get(f"/api/bridge/jobs/{job_id}")
        assert res2.status_code == 200
        data = res2.json()
        assert data["id"] == job_id
        assert data["card_id"] == card_id
        assert data["status"] == "pending"
        assert "created_at" in data


# ── GET /api/bridge/jobs/next/pending ─────────────────────────────────────────

class TestGetNextPending:

    def test_returns_null_when_no_pending_jobs(self, client):
        res = client.get("/api/bridge/jobs/next/pending")
        assert res.status_code == 200
        assert res.json()["job"] is None

    def test_claims_job_and_sets_running(self, client):
        card_id = _make_card(spec="spec")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending")
        assert res.status_code == 200
        job = res.json()["job"]
        assert job is not None
        assert job["status"] == "running"

    def test_includes_prompt_and_spec_in_response(self, client):
        card_id = _make_card("Feature X", spec="## Spec\nDo the thing")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending")
        job = res.json()["job"]
        assert "prompt" in job
        assert "spec" in job
        assert job["prompt"] is not None
        assert "Feature X" in job["prompt"]

    def test_second_call_returns_null(self, client):
        card_id = _make_card(spec="spec")
        client.post("/api/bridge/jobs", json={"card_id": card_id})
        client.get("/api/bridge/jobs/next/pending")  # claims it

        res = client.get("/api/bridge/jobs/next/pending")
        assert res.json()["job"] is None

    def test_lazy_prompt_build_for_telegram_queued_job(self, client):
        """Jobs queued via Telegram have no prompt_snapshot — it should be built lazily."""
        card_id = _make_card("Lazy feature", spec="## Spec\nBuild it")
        # Insert a job without prompt_snapshot (as Telegram does)
        with TestSession() as db:
            job = models.BridgeJob(
                card_id=card_id,
                status="pending",
                spec_snapshot="## Spec\nBuild it",
                prompt_snapshot=None,
                created_at=datetime.now(timezone.utc),
            )
            db.add(job)
            db.commit()

        res = client.get("/api/bridge/jobs/next/pending")
        job_data = res.json()["job"]
        assert job_data is not None
        assert job_data["prompt"] is not None
        assert "Lazy feature" in job_data["prompt"]
        assert "Build it" in job_data["prompt"]

    def test_fifo_order(self, client):
        """Oldest pending job is returned first."""
        card1 = _make_card("First", spec="s1")
        card2 = _make_card("Second", spec="s2")
        client.post("/api/bridge/jobs", json={"card_id": card1})
        client.post("/api/bridge/jobs", json={"card_id": card2})

        res = client.get("/api/bridge/jobs/next/pending")
        assert res.json()["job"]["card_id"] == card1

    def test_includes_card_title_for_slug_generation(self, client):
        card_id = _make_card("Fix login bug", spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending")
        job = res.json()["job"]
        assert job["card_title"] == "Fix login bug"


# ── POST /api/bridge/jobs/{id}/start ─────────────────────────────────────────

class TestStartJob:

    def test_records_branch_and_agent(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/start",
                          json={"branch": "qtask/7-fix-login", "agent": "work-mac"})
        assert res.status_code == 200
        data = res.json()
        assert data["branch_name"] == "qtask/7-fix-login"
        assert data["agent_name"] == "work-mac"

    def test_branch_and_agent_visible_via_get(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start",
                    json={"branch": "qtask/7-fix-login", "agent": "work-mac"})

        res = client.get(f"/api/bridge/jobs/{job_id}")
        assert res.json()["branch_name"] == "qtask/7-fix-login"
        assert res.json()["agent_name"] == "work-mac"

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/start",
                          json={"branch": "qtask/1-foo", "agent": "x"})
        assert res.status_code == 404


# ── POST /api/bridge/jobs/{id}/output ────────────────────────────────────────

class TestJobOutput:

    def _make_running_job(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.get("/api/bridge/jobs/next/pending")  # sets to running
        return job_id

    def test_appends_output_chunks(self, client):
        job_id = self._make_running_job(client)
        client.post(f"/api/bridge/jobs/{job_id}/output", json={"output": "line one\n"})
        client.post(f"/api/bridge/jobs/{job_id}/output", json={"output": "line two\n"})

        res = client.get(f"/api/bridge/jobs/{job_id}")
        assert "line one" in res.json()["output"]
        assert "line two" in res.json()["output"]

    def test_truncates_to_200_lines(self, client):
        job_id = self._make_running_job(client)
        # Post 250 lines in one chunk
        big_output = "\n".join(f"line {i}" for i in range(250))
        client.post(f"/api/bridge/jobs/{job_id}/output", json={"output": big_output})

        res = client.get(f"/api/bridge/jobs/{job_id}")
        stored = res.json()["output"]
        assert stored.count("\n") <= 200
        # Should keep the tail (most recent lines)
        assert "line 249" in stored
        assert "line 0" not in stored

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/output", json={"output": "x"})
        assert res.status_code == 404


# ── POST /api/bridge/jobs/{id}/complete ───────────────────────────────────────

class TestCompleteJob:

    def test_sets_status_done_and_result(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.get("/api/bridge/jobs/next/pending")  # set to running

        res = client.post(f"/api/bridge/jobs/{job_id}/complete",
                          json={"result": "https://github.com/owner/repo/pull/42"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "done"
        assert data["result"] == "https://github.com/owner/repo/pull/42"

    def test_empty_result_allowed(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/complete", json={})
        assert res.status_code == 200
        assert res.json()["result"] == ""

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/complete", json={})
        assert res.status_code == 404


# ── POST /api/bridge/jobs/{id}/error ─────────────────────────────────────────

class TestErrorJob:

    def test_sets_status_error_with_message(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/error",
                          json={"result": "claude not found on PATH"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "error"
        assert "claude" in data["result"]

    def test_default_error_message_when_empty(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/error", json={})
        assert res.status_code == 200
        assert res.json()["status"] == "error"

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/error", json={})
        assert res.status_code == 404


# ── GET /api/bridge/jobs/card/{id}/latest ────────────────────────────────────

class TestLatestCardJob:

    def test_returns_null_when_no_jobs(self, client):
        res = client.get("/api/bridge/jobs/card/9999/latest")
        assert res.status_code == 200
        assert res.json()["job"] is None

    def test_returns_most_recent_job(self, client):
        card_id = _make_card(spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id})
        client.get("/api/bridge/jobs/next/pending")
        client.post(f"/api/bridge/jobs/1/complete", json={"result": "done1"})
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get(f"/api/bridge/jobs/card/{card_id}/latest")
        assert res.status_code == 200
        job = res.json()["job"]
        assert job is not None
        # Most recent job is the second one (pending), not the first (done)
        assert job["status"] == "pending"


# ── GET /api/bridge/install.py ────────────────────────────────────────────────

class TestInstallScript:

    def test_returns_python_text(self, client):
        res = client.get("/api/bridge/install.py")
        assert res.status_code == 200
        assert "python" in res.text.lower() or "import" in res.text

    def test_contains_main_function(self, client):
        res = client.get("/api/bridge/install.py")
        assert "def main" in res.text

    def test_contains_install_dir(self, client):
        res = client.get("/api/bridge/install.py")
        assert "qtask-bridge" in res.text

    def test_writes_claude_toml(self, client):
        res = client.get("/api/bridge/install.py")
        assert "claude.toml" in res.text

    def test_claude_toml_only_written_if_not_exists(self, client):
        res = client.get("/api/bridge/install.py")
        assert "already exists" in res.text  # skips on reinstall

    def test_toml_template_documents_setup_cmd(self, client):
        res = client.get("/api/bridge/install.py")
        assert "setup_cmd" in res.text

    def test_toml_template_documents_per_repo_table_form(self, client):
        res = client.get("/api/bridge/install.py")
        assert '[repos."owner/api"]' in res.text

    def test_usage_mentions_tag_and_cleanup(self, client):
        res = client.get("/api/bridge/install.py")
        assert "--tag" in res.text
        assert "--cleanup" in res.text

    def test_toml_template_content_is_not_indented_at_runtime(self, client):
        """Regression guard: TOML_TEMPLATE is nested inside its own
        textwrap.dedent() specifically so the *outer* script dedent (which
        strips a shared 8-space prefix) isn't defeated by a column-0 line —
        but that only matters if the nested dedent actually resolves back to
        column 0 when the install script itself runs. Execute that part for
        real rather than just eyeballing the source text."""
        res = client.get("/api/bridge/install.py")
        ns = {}
        # Stop before main() is invoked — we only need the module-level
        # TOML_TEMPLATE assignment, not to actually run the installer.
        source = res.text.rsplit("def main():", 1)[0]
        exec(compile(source, "install.py", "exec"), ns)
        assert ns["TOML_TEMPLATE"].startswith("# qtask-bridge configuration")
        for line in ns["TOML_TEMPLATE"].splitlines():
            if line.strip():
                assert not line.startswith(" "), f"indented toml line: {line!r}"


# ── Served scripts compile as valid Python ───────────────────────────────────

class TestServedScriptsCompile:
    """Both scripts are Python source embedded as triple-quoted strings —
    nothing statically checks their syntax, so a bad edit could silently ship
    a script that fails the moment a user actually runs it. Compile them for
    real on every test run to catch that."""

    def test_agent_script_compiles(self, client):
        res = client.get("/api/bridge/agent.py")
        compile(res.text, "agent.py", "exec")

    def test_install_script_compiles(self, client):
        res = client.get("/api/bridge/install.py")
        compile(res.text, "install.py", "exec")

    def test_agent_script_top_level_statements_are_not_indented(self, client):
        """Regression guard for the same class of dedent bug as install.py:
        the first real statement (the module docstring) must start at column 0."""
        res = client.get("/api/bridge/agent.py")
        lines = res.text.splitlines()
        first_code_line = next(l for l in lines if l.strip() and not l.startswith("#!"))
        assert not first_code_line.startswith(" "), repr(first_code_line)


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


# ── GET /api/bridge/jobs/next/pending?repos= ─────────────────────────────────

class TestReposFilter:

    def test_returns_matching_repo_job(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/myapp/issues/1")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/myapp")
        assert res.json()["job"] is not None

    def test_excludes_other_repo_job(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/other/issues/1")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/myapp")
        assert res.json()["job"] is None

    def test_null_target_repo_returned_when_filter_set(self, client):
        """Jobs with no target_repo (no GitHub link) should be returned to any bridge."""
        card_id = _make_card(spec="s")  # no external_id → target_repo=None
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/myapp")
        assert res.json()["job"] is not None

    def test_no_filter_returns_any_repo_job(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/other/issues/1")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending")
        assert res.json()["job"] is not None

    def test_multiple_repos_in_filter(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/second/issues/5")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/first,owner/second")
        assert res.json()["job"] is not None

    def test_multiple_repos_excludes_non_matching(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/third/issues/5")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/first,owner/second")
        assert res.json()["job"] is None
