"""
Tests for the bridge job queue endpoints (routers/bridge.py).

Covers:
  - POST /api/bridge/jobs          — queue job (no spec → 400, valid → 200)
  - GET  /api/bridge/jobs/{id}     — get status
  - GET  /api/bridge/jobs/next/pending — atomic claim, lazy prompt build, double-claim
  - POST /api/bridge/jobs/{id}/complete
  - POST /api/bridge/jobs/{id}/error
  - GET  /api/bridge/jobs/card/{id}/latest
  - GET  /api/bridge/install.py    — installer script content
  - GET  /api/bridge/agent.py      — agent script content
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

def _make_card(title="Test card", spec=None, external_id=None, description=None):
    with TestSession() as db:
        card = models.Card(
            title=title, section="today", position=0,
            spec=spec, external_id=external_id, description=description,
        )
        db.add(card)
        db.commit()
        db.refresh(card)
        return card.id


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
        assert "todo-bridge" in res.text


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
