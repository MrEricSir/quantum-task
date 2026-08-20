"""
Tests for POST/DELETE /api/health/measurements (routers/health.py) -- manual health entry.

Uses FastAPI's TestClient with an in-memory SQLite database -- no backend network calls.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from main import app
from deps import get_db

TEST_DB_URL = "sqlite://"

test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=test_engine)
    yield
    models.Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestCreateHealthMeasurement:
    def test_creates_manual_entry(self, client):
        res = client.post("/api/health/measurements", json={
            "date": "2026-08-19", "metric": "weight", "value": 70.5,
        })
        assert res.status_code == 201
        body = res.json()
        assert body["date"] == "2026-08-19"
        assert body["metric"] == "weight"
        assert body["value"] == 70.5
        assert body["source"] == "manual"
        assert isinstance(body["id"], int)

    def test_upserts_same_date_and_metric(self, client):
        client.post("/api/health/measurements", json={"date": "2026-08-19", "metric": "steps", "value": 5000})
        res = client.post("/api/health/measurements", json={"date": "2026-08-19", "metric": "steps", "value": 8000})
        assert res.status_code == 201
        with TestingSessionLocal() as db:
            rows = db.query(models.WithingsMeasurement).filter_by(date="2026-08-19", metric="steps").all()
        assert len(rows) == 1
        assert rows[0].value == 8000

    def test_rejects_unknown_metric(self, client):
        res = client.post("/api/health/measurements", json={
            "date": "2026-08-19", "metric": "vo2max", "value": 50,
        })
        assert res.status_code == 400

    def test_rejects_malformed_date(self, client):
        res = client.post("/api/health/measurements", json={
            "date": "08/19/2026", "metric": "weight", "value": 70,
        })
        assert res.status_code == 400

    def test_accepts_every_manual_metric(self, client):
        from routers.health import MANUAL_METRICS
        for i, metric in enumerate(MANUAL_METRICS):
            res = client.post("/api/health/measurements", json={
                "date": "2026-08-19", "metric": metric, "value": 1.0 + i,
            })
            assert res.status_code == 201, f"{metric} rejected: {res.text}"

    def test_manual_steps_meeting_habit_goal_auto_completes_habit(self, client):
        tag_res = client.post("/api/tags", json={"name": "health", "color": "#000"})
        habit_res = client.post("/api/habits", json={
            "name": "Walk", "health_metric": "steps", "health_goal": 5000, "tag_ids": [],
        })
        assert habit_res.status_code == 201
        habit_id = habit_res.json()["id"]

        today = date.today().isoformat()
        res = client.post("/api/health/measurements", json={"date": today, "metric": "steps", "value": 6000})
        assert res.status_code == 201

        habits = client.get("/api/habits").json()
        habit = next(h for h in habits if h["id"] == habit_id)
        assert habit["completed_today"] is True


class TestDeleteHealthMeasurement:
    def test_deletes_existing_entry(self, client):
        created = client.post("/api/health/measurements", json={
            "date": "2026-08-19", "metric": "weight", "value": 70.5,
        }).json()
        res = client.delete(f"/api/health/measurements/{created['id']}")
        assert res.status_code == 200
        with TestingSessionLocal() as db:
            assert db.query(models.WithingsMeasurement).filter_by(id=created["id"]).first() is None

    def test_404_for_missing_entry(self, client):
        res = client.delete("/api/health/measurements/999999")
        assert res.status_code == 404


class TestHealthDataIncludesSourceAndId:
    def test_manual_entries_appear_in_withings_health_data_with_source(self, client):
        today = date.today().isoformat()
        client.post("/api/health/measurements", json={"date": today, "metric": "weight", "value": 70.5})
        res = client.get("/api/withings/health-data?days=30")
        assert res.status_code == 200
        measurements = res.json()["measurements"]
        assert len(measurements) == 1
        assert measurements[0]["source"] == "manual"
        assert isinstance(measurements[0]["id"], int)
