"""
Tests for the manual-check guard on auto-tracked habits (routers/habits.py).

Habits are blocked from manual check/uncheck ONLY if they have withings_metric
set (Withings auto-tracks them).  Experiment habits (is_experiment=True, but no
withings_metric) are manually checkable — the user must mark them done.
"""
import sys
import os
from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Base
import models
from routers import habits as habits_router
from deps import get_db

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(habits_router.router)
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


LOCAL_DATE = "2026-06-20"
HEADERS = {"X-Local-Date": LOCAL_DATE}


def _habit(db, name: str, withings_metric=None, withings_goal=None) -> models.Habit:
    h = models.Habit(name=name, withings_metric=withings_metric, withings_goal=withings_goal)
    db.add(h)
    db.flush()
    return h


def _experiment(db, habit_id: int, status: str = "active") -> models.HealthExperiment:
    exp = models.HealthExperiment(
        week="2026-W25",
        text="Test experiment",
        needs_habit=True,
        habit_id=habit_id,
        status=status,
    )
    db.add(exp)
    db.flush()
    return exp


# ── Check endpoint ────────────────────────────────────────────────────────────

class TestCheckHabit:

    def test_regular_habit_can_be_checked(self, client, db_session):
        h = _habit(db_session, "Morning meditation")
        db_session.commit()
        r = client.post(f"/api/habits/{h.id}/check", headers=HEADERS)
        assert r.status_code == 200

    def test_withings_metric_habit_blocked(self, client, db_session):
        h = _habit(db_session, "Walk 10k steps", withings_metric="steps", withings_goal=10_000)
        db_session.commit()
        r = client.post(f"/api/habits/{h.id}/check", headers=HEADERS)
        assert r.status_code == 403

    def test_experiment_habit_without_withings_can_be_checked(self, client, db_session):
        """Experiment habits with no withings_metric are manually checkable."""
        h = _habit(db_session, "🧪 1 hour screen-free time")  # no withings_metric
        _experiment(db_session, habit_id=h.id, status="active")
        db_session.commit()
        r = client.post(f"/api/habits/{h.id}/check", headers=HEADERS)
        assert r.status_code == 200

    def test_dismissed_experiment_habit_can_be_checked(self, client, db_session):
        """Dismissed experiment habits also remain manually checkable."""
        h = _habit(db_session, "🧪 1 hour screen-free time")
        _experiment(db_session, habit_id=h.id, status="dismissed")
        db_session.commit()
        r = client.post(f"/api/habits/{h.id}/check", headers=HEADERS)
        assert r.status_code == 200

    def test_nonexistent_habit_returns_404(self, client):
        r = client.post("/api/habits/9999/check", headers=HEADERS)
        assert r.status_code == 404


# ── Uncheck endpoint ──────────────────────────────────────────────────────────

class TestUncheckHabit:

    def test_regular_habit_can_be_unchecked(self, client, db_session):
        h = _habit(db_session, "Evening walk")
        db_session.add(models.HabitCompletion(habit_id=h.id, date=LOCAL_DATE))
        db_session.commit()
        r = client.delete(f"/api/habits/{h.id}/check", headers=HEADERS)
        assert r.status_code == 200

    def test_withings_metric_habit_blocked(self, client, db_session):
        h = _habit(db_session, "Walk 10k steps", withings_metric="steps", withings_goal=10_000)
        db_session.add(models.HabitCompletion(habit_id=h.id, date=LOCAL_DATE))
        db_session.commit()
        r = client.delete(f"/api/habits/{h.id}/check", headers=HEADERS)
        assert r.status_code == 403

    def test_experiment_habit_without_withings_can_be_unchecked(self, client, db_session):
        """Experiment habits with no withings_metric are manually uncheckable."""
        h = _habit(db_session, "🧪 1 hour screen-free time")  # no withings_metric
        _experiment(db_session, habit_id=h.id, status="active")
        db_session.add(models.HabitCompletion(habit_id=h.id, date=LOCAL_DATE))
        db_session.commit()
        r = client.delete(f"/api/habits/{h.id}/check", headers=HEADERS)
        assert r.status_code == 200

    def test_dismissed_experiment_habit_can_be_unchecked(self, client, db_session):
        """Dismissed experiment habits also remain manually uncheckable."""
        h = _habit(db_session, "🧪 1 hour screen-free time")
        _experiment(db_session, habit_id=h.id, status="dismissed")
        db_session.add(models.HabitCompletion(habit_id=h.id, date=LOCAL_DATE))
        db_session.commit()
        r = client.delete(f"/api/habits/{h.id}/check", headers=HEADERS)
        assert r.status_code == 200
