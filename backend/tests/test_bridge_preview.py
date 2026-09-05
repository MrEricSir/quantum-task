"""
Tests for the bridge-managed preview server's server-side half:
  POST /api/bridge/jobs/{id}/preview  -- status/url reporting from the CLI's
                                          _start_preview/_report_preview_when_ready/
                                          _kill_preview_if_running

The CLI-side detached-launch/PID-file/kill logic is covered in test_bridge_scripts.py
(TestStartPreview, TestReportPreviewWhenReady, TestKillPreviewIfRunning, TestCmdStopPreview) --
this file owns only the endpoint and its effect on the job row. See PRODUCT_NOTES.md's
"Bridge-managed preview server" entry.
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


def _make_card():
    with TestSession() as db:
        card = models.Card(title="Feature", section="today", position=0, spec="## Spec\ndo it")
        db.add(card)
        db.commit()
        db.refresh(card)
        return card.id


def _make_job(card_id, status="done"):
    with TestSession() as db:
        job = models.BridgeJob(
            card_id=card_id, status=status, created_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id


class TestUpdateJobPreviewEndpoint:

    def test_404_for_unknown_job(self, client):
        res = client.post("/api/bridge/jobs/999999/preview", json={"status": "starting"})
        assert res.status_code == 404

    def test_sets_starting_status_with_no_url(self, client):
        card_id = _make_card()
        job_id = _make_job(card_id)

        res = client.post(f"/api/bridge/jobs/{job_id}/preview", json={"status": "starting"})
        assert res.status_code == 200
        body = res.json()
        assert body["preview_status"] == "starting"
        assert body["preview_url"] is None

    def test_sets_running_status_with_url(self, client):
        card_id = _make_card()
        job_id = _make_job(card_id)

        res = client.post(f"/api/bridge/jobs/{job_id}/preview", json={
            "status": "running", "url": "http://localhost:20771",
        })
        assert res.status_code == 200
        body = res.json()
        assert body["preview_status"] == "running"
        assert body["preview_url"] == "http://localhost:20771"

    def test_a_later_call_overwrites_the_previous_preview_state(self, client):
        card_id = _make_card()
        job_id = _make_job(card_id)
        client.post(f"/api/bridge/jobs/{job_id}/preview", json={
            "status": "running", "url": "http://localhost:20771",
        })

        res = client.post(f"/api/bridge/jobs/{job_id}/preview", json={"status": "stopped"})
        body = res.json()
        assert body["preview_status"] == "stopped"
        assert body["preview_url"] is None

    def test_round_trips_through_get_job(self, client):
        card_id = _make_card()
        job_id = _make_job(card_id)
        client.post(f"/api/bridge/jobs/{job_id}/preview", json={
            "status": "running", "url": "http://localhost:20771",
        })

        res = client.get(f"/api/bridge/jobs/{job_id}")
        body = res.json()
        assert body["preview_status"] == "running"
        assert body["preview_url"] == "http://localhost:20771"

    def test_never_touches_the_jobs_own_status(self, client):
        """A job can be "done" while its preview keeps running afterward -- preview
        transitions are a fully independent lifecycle."""
        card_id = _make_card()
        job_id = _make_job(card_id, status="done")

        res = client.post(f"/api/bridge/jobs/{job_id}/preview", json={
            "status": "running", "url": "http://localhost:20771",
        })

        assert res.json()["status"] == "done"
        with TestSession() as db:
            assert db.query(models.BridgeJob).filter_by(id=job_id).first().status == "done"
