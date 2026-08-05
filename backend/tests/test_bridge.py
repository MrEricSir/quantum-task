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

import subprocess
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

def _load_agent_module(script_text):
    """Exec the served agent.py text and return its module namespace, for
    tests that call real functions instead of just asserting on strings.
    The script unconditionally calls main() at the bottom (no __main__
    guard) -- with no matching CLI args in this process, argparse exits via
    SystemExit, which is expected and swallowed here."""
    namespace = {"__name__": "agent_under_test"}
    try:
        exec(compile(script_text, "agent.py", "exec"), namespace)  # noqa: S102
    except SystemExit:
        pass
    return namespace


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


def _get_install_script(client):
    """GET /api/bridge/install.py requires ?token=<bridge install token>, so tests
    that just want the script content fetch a fresh token first."""
    token = client.get("/api/bridge/install-token").json()["token"]
    return client.get(f"/api/bridge/install.py?token={token}")


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

    def test_records_worktree_path(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/start", json={
            "branch": "qtask/7-fix-login", "agent": "work-mac",
            "worktree_path": "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-fix-login",
        })
        assert res.status_code == 200
        assert res.json()["worktree_path"] == "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-fix-login"

    def test_worktree_path_is_optional(self, client):
        """Older bridge versions won't send it -- must not break."""
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/start",
                          json={"branch": "qtask/7-fix-login", "agent": "work-mac"})
        assert res.status_code == 200
        assert res.json()["worktree_path"] is None

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
        column 0 when the install script itself runs. Execute that part for
        real rather than just eyeballing the source text."""
        res = _get_install_script(client)
        ns = {}
        # Stop before main() is invoked — we only need the module-level
        # TOML_TEMPLATE assignment, not to actually run the installer.
        source = res.text.rsplit("def main():", 1)[0]
        exec(compile(source, "install.py", "exec"), ns)
        assert ns["TOML_TEMPLATE"].startswith("# qtask-bridge configuration")
        for line in ns["TOML_TEMPLATE"].splitlines():
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
        ns = self._load_install_module_without_running_main(client)
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

        monkeypatch.setattr(ns["subprocess"], "run", fake_run)
        real_expanduser = os.path.expanduser
        monkeypatch.setattr(
            ns["os"].path, "expanduser",
            lambda p: str(fake_excludes_path) if "ignore_qtask_bridge" in p else real_expanduser(p),
        )
        ns["print"] = lambda *a, **k: None

        ns["setup_global_gitignore"]()

        assert git_config_set_calls == [["git", "config", "--global", "core.excludesFile", str(fake_excludes_path)]]
        assert fake_excludes_path.exists()
        content = fake_excludes_path.read_text()
        for entry in ns["BRIDGE_IGNORE_ENTRIES"]:
            assert entry in content

    def test_global_gitignore_appends_to_existing_excludes_file_when_already_set(self, client, monkeypatch, tmp_path):
        """If the user already has a core.excludesFile configured, append
        to it -- never silently redirect git to a different file."""
        ns = self._load_install_module_without_running_main(client)
        existing_excludes = tmp_path / "my-own-global-gitignore"
        existing_excludes.write_text("*.swp\n")

        def fake_run(cmd, **kwargs):
            class Result:
                stdout = str(existing_excludes) + "\n"
                returncode = 0
            if cmd[:4] == ["git", "config", "--global", "--get"]:
                return Result()
            raise AssertionError(f"should not reconfigure an already-set excludesFile: {cmd}")

        monkeypatch.setattr(ns["subprocess"], "run", fake_run)
        ns["print"] = lambda *a, **k: None

        ns["setup_global_gitignore"]()

        content = existing_excludes.read_text()
        assert "*.swp" in content  # untouched
        for entry in ns["BRIDGE_IGNORE_ENTRIES"]:
            assert entry in content

    def test_global_gitignore_is_idempotent(self, client, monkeypatch, tmp_path):
        """Re-running the installer (e.g. to pick up a bridge update) must
        not duplicate entries on every run."""
        ns = self._load_install_module_without_running_main(client)
        excludes_path = tmp_path / "ignore_qtask_bridge"
        excludes_path.write_text("\n".join(ns["BRIDGE_IGNORE_ENTRIES"]) + "\n")

        def fake_run(cmd, **kwargs):
            class Result:
                stdout = str(excludes_path) + "\n"
                returncode = 0
            return Result()

        monkeypatch.setattr(ns["subprocess"], "run", fake_run)
        ns["print"] = lambda *a, **k: None

        ns["setup_global_gitignore"]()

        content = excludes_path.read_text()
        for entry in ns["BRIDGE_IGNORE_ENTRIES"]:
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

    def _load_install_module_without_running_main(self, client):
        """Compile everything up to (but not including) the script's own
        trailing top-level `main()` call, so importing it for inspection
        doesn't actually run the installer for real. The full served script
        always ends with an unguarded `main()` call at column 0 -- there's no
        `if __name__ == "__main__":` guard -- so exec'ing res.text verbatim
        would install the bridge for real against whatever ALLOWED_ORIGIN
        happens to be reachable, which is exactly the bug this helper avoids."""
        res = _get_install_script(client)
        source = res.text.rsplit("\nmain()", 1)[0]
        ns = {}
        exec(compile(source, "install.py", "exec"), ns)
        return ns

    def test_ssl_cert_error_shows_actionable_message_instead_of_raw_traceback(self, client, monkeypatch):
        """Regression test for a real failure seen in the wild: the official
        python.org macOS installer doesn't ship a CA bundle, so urlopen raises
        ssl.SSLCertVerificationError while downloading agent.py. main() should
        catch that specific error and print a fix instead of a raw traceback."""
        import ssl
        import urllib.error

        ns = self._load_install_module_without_running_main(client)

        class _FailingOpener:
            def __enter__(self):
                raise urllib.error.URLError(ssl.SSLCertVerificationError("cert verify failed"))

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(ns["urllib"].request, "urlopen", lambda req: _FailingOpener())

        printed = []
        ns["print"] = lambda *args, **kwargs: printed.append(" ".join(str(a) for a in args))

        with pytest.raises(SystemExit) as exc_info:
            ns["main"]()
        assert exc_info.value.code == 1

        output = "\n".join(printed)
        assert "Install Certificates.command" in output
        assert "Traceback" not in output

    def test_other_url_errors_still_propagate(self, client, monkeypatch):
        """Only the specific SSL-cert failure gets a friendly message — any
        other download failure (network down, DNS, etc.) should still raise
        normally rather than being silently swallowed."""
        import urllib.error

        ns = self._load_install_module_without_running_main(client)

        def _raise_generic(req):
            raise urllib.error.URLError("network is unreachable")

        monkeypatch.setattr(ns["urllib"].request, "urlopen", _raise_generic)
        ns["print"] = lambda *args, **kwargs: None

        with pytest.raises(urllib.error.URLError):
            ns["main"]()


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
        res = _get_install_script(client)
        compile(res.text, "install.py", "exec")

    def test_agent_script_top_level_statements_are_not_indented(self, client):
        """Regression guard for the same class of dedent bug as install.py:
        the first real statement (the module docstring) must start at column 0."""
        res = client.get("/api/bridge/agent.py")
        lines = res.text.splitlines()
        first_code_line = next(l for l in lines if l.strip() and not l.startswith("#!"))
        assert not first_code_line.startswith(" "), repr(first_code_line)


class TestBridgeScriptsExemptFromAuth:
    """Only install.py is exempt from AuthMiddleware's session-cookie/bearer-token
    check — that's the entire point of a "pre-authed" install script: a machine
    with no prior credentials must be able to `curl` it. It's gated instead by
    its own scoped, rotatable install token (see TestInstallScript), so exemption
    from the *global* check doesn't mean exemption from all checks.

    agent.py is NOT exempt: the install script always fetches it with a real
    `Authorization: Bearer <AUTH_PASSWORD>` header (see get_install_script's
    generated `main()`), so it never needs a bare, credential-free fetch path.

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

    def test_writes_claude_statusline_settings(self, client):
        res = client.get("/api/bridge/agent.py")
        assert "def _write_claude_settings" in res.text
        assert "statusLine" in res.text
        assert ".claude" in res.text
        assert "settings.local.json" in res.text
        # Called from run_job for every job, not just some code path that's dead
        assert "_write_claude_settings(worktree_path)" in res.text

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
        """Otherwise the file is just sitting there and Claude has to
        stumble onto it -- the whole point is it doesn't have to."""
        res = client.get("/api/bridge/agent.py")
        assert "def _make_prompt" in res.text
        prompt_start = res.text.index("def _make_prompt")
        prompt_end = res.text.index("def _detect_primary_branch")
        # {ENV_FILENAME}, not the literal ".env.qtask" -- this is the served
        # SOURCE text, substitution only happens when the downloaded script runs.
        assert "{ENV_FILENAME}" in res.text[prompt_start:prompt_end]

    def test_env_content_actually_produces_valid_shell_syntax(self, client, tmp_path):
        """Execute the real function against a real directory rather than
        just asserting on strings -- confirms the file is genuinely
        sourceable shell syntax, not just text that looks plausible."""
        res = client.get("/api/bridge/agent.py")
        namespace = _load_agent_module(res.text)

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        namespace["_write_qtask_env"](str(worktree), 77)

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

    def test_port_range_derivation_is_deterministic_and_ten_wide(self, client, tmp_path):
        res = client.get("/api/bridge/agent.py")
        namespace = _load_agent_module(res.text)

        for job_id in (1, 400, 401, 799, 800):
            worktree = tmp_path / f"wt-{job_id}"
            worktree.mkdir()
            namespace["_write_qtask_env"](str(worktree), job_id)
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
