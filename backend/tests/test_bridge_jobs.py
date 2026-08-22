"""
Tests for the bridge job queue endpoints (bridge/router.py + bridge/jobs.py).

Covers:
  - POST /api/bridge/jobs              — queue job (no spec → 400, valid → 200)
  - POST /api/bridge/jobs/queue-by-tag — queue every eligible tagged card
  - GET  /api/bridge/jobs/{id}         — get status
  - GET  /api/bridge/jobs/next/pending — atomic claim, lazy prompt build, double-claim,
                                         ?repos= filtering
  - POST /api/bridge/jobs/{id}/start   — record branch + agent name
  - POST /api/bridge/jobs/{id}/output  — stdout chunking + line-cap truncation
  - POST /api/bridge/jobs/{id}/complete
  - POST /api/bridge/jobs/{id}/error
  - GET  /api/bridge/jobs/card/{id}/latest

Served-script tests (install.py/agent.py content) live in test_bridge_scripts.py.
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


def _make_comment(item_id, github_id, author="coderabbitai[bot]", body="Consider a set here.",
                   comment_type="pr_review_comment", diff_path="src/a.py", diff_line=10):
    with TestSession() as db:
        comment = models.EngineeringItemComment(
            item_id=item_id, github_id=github_id, author=author, body=body,
            comment_type=comment_type, diff_path=diff_path, diff_line=diff_line,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment.id


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


# ── POST /api/bridge/jobs/{id}/fix ───────────────────────────────────────────

class TestQueueFixJob:

    def _make_started_job(self, client, card_id=None, external_id=None):
        """A job that's already run to completion and recorded a resumable worktree -- status
        "done", not "pending", so it doesn't shadow a later fix job in next/pending's query
        (oldest pending job wins there, and this one was created first)."""
        card_id = card_id or _make_card(spec="s", external_id=external_id)
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start", json={
            "branch": "qtask/7-fix-login", "agent": "work-mac",
            "worktree_path": "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-fix-login",
        })
        client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "done"})
        return job_id

    def test_queues_a_fix_job_resuming_the_original_worktree(self, client):
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(item_id, github_id=501)
        job_id = self._make_started_job(client, external_id="github:owner/repo/pull/7")

        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [comment_id]})
        assert res.status_code == 200
        data = res.json()
        assert data["fix_of_job_id"] == job_id
        assert data["fix_comment_ids"] == [comment_id]
        assert data["status"] == "pending"
        # Copied from the original job at creation time, not left null for /start to fill in.
        assert data["branch_name"] == "qtask/7-fix-login"
        assert data["worktree_path"] == "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-fix-login"

    def test_fix_prompt_includes_comment_body_and_diff_position(self, client):
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(
            item_id, github_id=502, author="coderabbitai[bot]",
            body="Use a set for O(1) lookups.", diff_path="src/auth.js", diff_line=42,
        )
        job_id = self._make_started_job(client, external_id="github:owner/repo/pull/7")

        fix_job_id = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [comment_id]}).json()["id"]
        # prompt_snapshot isn't exposed on the plain job response -- fetch the next pending
        # job instead, matching how the bridge itself receives the actual prompt content.
        pending = client.get("/api/bridge/jobs/next/pending").json()["job"]
        assert pending["id"] == fix_job_id
        assert "Use a set for O(1) lookups." in pending["prompt"]
        assert "src/auth.js:42" in pending["prompt"]
        assert "coderabbitai[bot]" in pending["prompt"]
        assert "not a general invitation to refactor" in pending["prompt"]

    def test_404_for_missing_original_job(self, client):
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(item_id, github_id=503)
        res = client.post("/api/bridge/jobs/99999/fix", json={"comment_ids": [comment_id]})
        assert res.status_code == 404

    def test_400_when_original_job_has_no_worktree(self, client):
        """A job that never ran (still pending, no /start call) has nothing to resume."""
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(item_id, github_id=504)

        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [comment_id]})
        assert res.status_code == 400

    def test_400_for_empty_comment_ids(self, client):
        job_id = self._make_started_job(client)
        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": []})
        assert res.status_code == 400

    def test_404_for_unknown_comment_id(self, client):
        job_id = self._make_started_job(client)
        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [999999]})
        assert res.status_code == 404

    def test_multiple_comments_all_included_in_prompt(self, client):
        item_id = _make_eng_item("github:owner/repo/pull/7")
        c1 = _make_comment(item_id, github_id=505, body="First fix needed.", diff_path="a.py", diff_line=1)
        c2 = _make_comment(item_id, github_id=506, author="a-human-reviewer",
                            body="Second fix needed.", diff_path="b.py", diff_line=2)
        job_id = self._make_started_job(client, external_id="github:owner/repo/pull/7")

        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [c1, c2]})
        assert res.json()["fix_comment_ids"] == [c1, c2]

        pending = client.get("/api/bridge/jobs/next/pending").json()["job"]
        assert "First fix needed." in pending["prompt"]
        assert "Second fix needed." in pending["prompt"]
        assert "a-human-reviewer" in pending["prompt"]


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
