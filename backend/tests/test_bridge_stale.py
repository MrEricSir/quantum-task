"""
Tests for stale bridge job detection (bridge/stale.py) and the surrounding
wiring:
  POST /api/bridge/jobs/{id}/heartbeat  — bumps updated_at
  POST /api/bridge/jobs/check-stale     — unconditional DB transition +
                                           conditional Telegram notification
  telegram.scheduler.notify_stalled_jobs

A job stuck at "running" forever (crashed agent, sleeping laptop, dropped
network) previously had no automatic recovery path -- see bridge/stale.py's
module docstring for the heartbeat mechanism this relies on.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from bridge.stale import STALE_THRESHOLD_MINUTES, check_stale_bridge_jobs
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

def _make_job(status="running", minutes_ago=0, card_title="Test card"):
    """Create a card + bridge job with updated_at backdated by minutes_ago."""
    with TestSession() as db:
        card = models.Card(title=card_title, section="today", position=0, spec="s")
        db.add(card)
        db.commit()
        db.refresh(card)

        updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
        job = models.BridgeJob(
            card_id=card.id,
            status=status,
            created_at=datetime.now(timezone.utc),
            updated_at=updated_at,
            branch_name="qtask/1-test" if status != "pending" else None,
            agent_name="work-mac" if status != "pending" else None,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id


# ── bridge.stale.check_stale_bridge_jobs ───────────────────────────────────────

class TestCheckStaleBridgeJobs:

    def test_leaves_recent_running_job_alone(self):
        job_id = _make_job(status="running", minutes_ago=1)
        with TestSession() as db:
            stale = check_stale_bridge_jobs(db)
        assert stale == []
        with TestSession() as db:
            assert db.query(models.BridgeJob).filter_by(id=job_id).first().status == "running"

    def test_marks_old_running_job_stalled(self):
        job_id = _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 5)
        with TestSession() as db:
            stale = check_stale_bridge_jobs(db)
            assert len(stale) == 1
            assert stale[0].id == job_id
        with TestSession() as db:
            assert db.query(models.BridgeJob).filter_by(id=job_id).first().status == "stalled"

    def test_boundary_just_under_threshold_not_stale(self):
        _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES - 1)
        with TestSession() as db:
            stale = check_stale_bridge_jobs(db)
        assert stale == []

    def test_boundary_just_over_threshold_is_stale(self):
        _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 1)
        with TestSession() as db:
            stale = check_stale_bridge_jobs(db)
        assert len(stale) == 1

    def test_ignores_pending_jobs_even_when_old(self):
        """A job nobody has picked up yet isn't 'stalled' -- that's a
        different failure mode (no bridge running --watch), out of scope
        for this check."""
        _make_job(status="pending", minutes_ago=STALE_THRESHOLD_MINUTES + 100)
        with TestSession() as db:
            stale = check_stale_bridge_jobs(db)
        assert stale == []

    def test_ignores_done_and_error_jobs(self):
        _make_job(status="done", minutes_ago=STALE_THRESHOLD_MINUTES + 100)
        _make_job(status="error", minutes_ago=STALE_THRESHOLD_MINUTES + 100)
        with TestSession() as db:
            stale = check_stale_bridge_jobs(db)
        assert stale == []

    def test_idempotent_second_call_finds_nothing_new(self):
        _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 5)
        with TestSession() as db:
            first = check_stale_bridge_jobs(db)
        assert len(first) == 1
        with TestSession() as db:
            second = check_stale_bridge_jobs(db)
        assert second == []

    def test_multiple_stale_jobs_all_transitioned(self):
        id1 = _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 5)
        id2 = _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 50)
        with TestSession() as db:
            stale = check_stale_bridge_jobs(db)
            assert {j.id for j in stale} == {id1, id2}


# ── POST /api/bridge/jobs/{id}/heartbeat ───────────────────────────────────────

class TestHeartbeatEndpoint:

    def test_updates_updated_at(self, client):
        job_id = _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 5)
        res = client.post(f"/api/bridge/jobs/{job_id}/heartbeat")
        assert res.status_code == 200
        assert res.json() == {"ok": True}

        with TestSession() as db:
            job = db.query(models.BridgeJob).filter_by(id=job_id).first()
            age = datetime.now(timezone.utc).replace(tzinfo=None) - job.updated_at
            assert age < timedelta(minutes=1)

    def test_heartbeat_prevents_stale_transition(self, client):
        job_id = _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 5)
        client.post(f"/api/bridge/jobs/{job_id}/heartbeat")

        with TestSession() as db:
            stale = check_stale_bridge_jobs(db)
        assert stale == []

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/heartbeat")
        assert res.status_code == 404


# ── POST /api/bridge/jobs/check-stale ─────────────────────────────────────────

class TestCheckStaleEndpoint:

    def test_transitions_stale_job_without_telegram_configured(self, client):
        """The DB mutation must happen even when Telegram isn't set up --
        otherwise the frontend status badge would never update for a user
        who hasn't configured notifications."""
        job_id = _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 5)

        res = client.post("/api/bridge/jobs/check-stale")
        assert res.status_code == 200
        body = res.json()
        assert body["stalled"] == 1
        assert body["notified"] == 0

        with TestSession() as db:
            assert db.query(models.BridgeJob).filter_by(id=job_id).first().status == "stalled"

    def test_no_stale_jobs_is_a_noop(self, client):
        res = client.post("/api/bridge/jobs/check-stale")
        assert res.status_code == 200
        assert res.json() == {"stalled": 0, "notified": 0}

    def test_notifies_when_telegram_configured(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok", "chat_id": "123",
            "schedule_time": "07:30", "tz_offset": 0,
        })
        _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 5, card_title="Stuck feature")

        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            res = client.post("/api/bridge/jobs/check-stale")

        assert res.status_code == 200
        body = res.json()
        assert body["stalled"] == 1
        assert body["notified"] == 1
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        assert call_args[0] == "tok"
        assert call_args[1] == "123"
        assert "Stuck feature" in call_args[2]


# ── telegram.scheduler.notify_stalled_jobs ────────────────────────────────────

class TestNotifyStalledJobs:

    def test_sends_one_message_per_job(self, client):
        from telegram.scheduler import notify_stalled_jobs

        id1 = _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 5, card_title="Job A")
        id2 = _make_job(status="running", minutes_ago=STALE_THRESHOLD_MINUTES + 5, card_title="Job B")
        with TestSession() as db:
            jobs = db.query(models.BridgeJob).filter(models.BridgeJob.id.in_([id1, id2])).all()
            for j in jobs:
                j.status = "stalled"
            db.commit()

            with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
                sent = notify_stalled_jobs(db, "tok", "123", jobs)

        assert sent == 2
        assert mock_send.call_count == 2

    def test_returns_zero_when_send_fails(self, client):
        from telegram.scheduler import notify_stalled_jobs

        job_id = _make_job(status="stalled", minutes_ago=STALE_THRESHOLD_MINUTES + 5)
        with TestSession() as db:
            jobs = [db.query(models.BridgeJob).filter_by(id=job_id).first()]

            with patch("telegram.scheduler.send_message", return_value=False):
                sent = notify_stalled_jobs(db, "tok", "123", jobs)

        assert sent == 0
