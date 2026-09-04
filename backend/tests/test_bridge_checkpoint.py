"""
Tests for the bridge checkpoint gate:
  GET/PUT /api/bridge/checkpoint-patterns  -- global pattern list config
  POST /api/bridge/jobs/{id}/acknowledge   -- webapp-only, needs_confirmation -> done

See PRODUCT_NOTES.md / QTASK_WORKFLOW_REVIEW.md's "watch/unattended middle ground" entry.
The checkpoint-triggering side (bridge/scripts/agent_core.py's _match_checkpoint_patterns,
and the /needs-confirmation endpoint) is covered in test_bridge_scripts.py and
test_bridge_unblock.py respectively -- this file owns the pattern-storage CRUD and the
human-facing acknowledge action.
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


def _make_job(card_id, status="running"):
    with TestSession() as db:
        job = models.BridgeJob(
            card_id=card_id, status=status, created_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id


class TestCheckpointPatternsEndpoint:
    def test_defaults_to_empty_list(self, client):
        res = client.get("/api/bridge/checkpoint-patterns")
        assert res.status_code == 200
        assert res.json() == {"patterns": []}

    def test_save_then_get_round_trips(self, client):
        put_res = client.put("/api/bridge/checkpoint-patterns", json={
            "patterns": ["alembic/versions/*", "package.json"],
        })
        assert put_res.status_code == 200

        get_res = client.get("/api/bridge/checkpoint-patterns")
        assert get_res.json() == {"patterns": ["alembic/versions/*", "package.json"]}

    def test_saving_again_replaces_the_previous_list_not_appends(self, client):
        client.put("/api/bridge/checkpoint-patterns", json={"patterns": ["a/*"]})
        client.put("/api/bridge/checkpoint-patterns", json={"patterns": ["b/*", "c/*"]})

        res = client.get("/api/bridge/checkpoint-patterns")
        assert res.json() == {"patterns": ["b/*", "c/*"]}

    def test_saving_an_empty_list_clears_it(self, client):
        client.put("/api/bridge/checkpoint-patterns", json={"patterns": ["a/*"]})
        client.put("/api/bridge/checkpoint-patterns", json={"patterns": []})

        res = client.get("/api/bridge/checkpoint-patterns")
        assert res.json() == {"patterns": []}


class TestAcknowledgeEndpoint:
    def test_flips_needs_confirmation_to_done(self, client):
        card_id = _make_card()
        job_id = _make_job(card_id, status="needs_confirmation")

        res = client.post(f"/api/bridge/jobs/{job_id}/acknowledge")
        assert res.status_code == 200
        assert res.json()["status"] == "done"

        with TestSession() as db:
            assert db.query(models.BridgeJob).filter_by(id=job_id).first().status == "done"

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/999/acknowledge")
        assert res.status_code == 404

    @pytest.mark.parametrize("status", ["pending", "running", "done", "error", "stalled", "blocked"])
    def test_400_when_job_is_not_awaiting_confirmation(self, client, status):
        card_id = _make_card()
        job_id = _make_job(card_id, status=status)

        res = client.post(f"/api/bridge/jobs/{job_id}/acknowledge")
        assert res.status_code == 400

        with TestSession() as db:
            assert db.query(models.BridgeJob).filter_by(id=job_id).first().status == status
