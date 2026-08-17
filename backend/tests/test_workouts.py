"""
Tests for the workout log router (routers/workouts.py).

Uses FastAPI TestClient with an in-memory SQLite DB.
LLM parsing is mocked to avoid network calls.
"""
import sys
import os
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Base
import models
from routers import workouts as workouts_router
from routers.correlations import _current_isoweek
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
    app.include_router(workouts_router.router)
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


# ── Mock LLM ──────────────────────────────────────────────────────────────────

def _mock_parse_row(raw: str) -> dict:
    return {"type": "row", "value": 5000.0, "unit": "m", "notes": None}

def _mock_parse_run(raw: str) -> dict:
    return {"type": "run", "value": 3.0, "unit": "mi", "notes": None}

def _mock_parse_strength(raw: str) -> dict:
    return {"type": "strength", "value": 185.0, "unit": "lbs", "notes": None}

def _mock_parse_yoga(raw: str) -> dict:
    return {"type": "yoga", "value": None, "unit": None, "notes": None}


# ── CRUD ──────────────────────────────────────────────────────────────────────

class TestWorkoutCRUD:

    def test_create_returns_parsed_entry(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        r = client.post("/api/workouts", json={"raw_input": "rowed 5000m"})
        assert r.status_code == 201
        data = r.json()[0]
        assert data["type"] == "row"
        assert data["value"] == 5000.0
        assert data["unit"] == "m"
        assert "id" in data
        assert "logged_at" in data

    def test_create_strength_entry(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_strength)
        r = client.post("/api/workouts", json={"raw_input": "bench pressed 185 lbs"})
        assert r.status_code == 201
        data = r.json()[0]
        assert data["type"] == "strength"
        assert data["value"] == 185.0
        assert data["unit"] == "lbs"

    def test_create_yoga_no_value(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_yoga)
        r = client.post("/api/workouts", json={"raw_input": "yoga"})
        assert r.status_code == 201
        data = r.json()[0]
        assert data["type"] == "yoga"
        assert data["value"] is None
        assert data["unit"] is None

    def test_create_requires_raw_input(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        r = client.post("/api/workouts", json={"raw_input": ""})
        assert r.status_code == 422

    def test_create_missing_raw_input(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        r = client.post("/api/workouts", json={})
        assert r.status_code == 422

    def test_get_entries_for_date(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        client.post("/api/workouts", json={"raw_input": "rowed 5000m", "logged_at": "2026-07-25T10:00:00"})
        r = client.get("/api/workouts?date_str=2026-07-25")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["type"] == "row"

    def test_get_entries_empty_date(self, client):
        r = client.get("/api/workouts?date_str=2026-01-01")
        assert r.status_code == 200
        assert r.json() == []

    def test_get_entries_excludes_other_dates(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        client.post("/api/workouts", json={"raw_input": "rowed", "logged_at": "2026-07-24T10:00:00"})
        r = client.get("/api/workouts?date_str=2026-07-25")
        assert r.status_code == 200
        assert r.json() == []

    def test_delete_removes_entry(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        created = client.post("/api/workouts", json={"raw_input": "rowed 5000m", "logged_at": "2026-07-25T10:00:00"}).json()[0]
        r = client.delete(f"/api/workouts/{created['id']}")
        assert r.status_code == 200
        entries = client.get("/api/workouts?date_str=2026-07-25").json()
        assert entries == []

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/api/workouts/99999")
        assert r.status_code == 404


# ── Update ──────────────────────────────────────────────────────────────────────

class TestWorkoutUpdate:

    def _create(self, client, monkeypatch, raw_input="rowed 5000m"):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        return client.post("/api/workouts", json={"raw_input": raw_input}).json()[0]

    def test_updates_type(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/workouts/{entry['id']}", json={"type": "run"})
        assert r.status_code == 200
        assert r.json()["type"] == "run"

    def test_invalid_type_is_ignored(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/workouts/{entry['id']}", json={"type": "not-a-type"})
        assert r.status_code == 200
        assert r.json()["type"] == "row"

    def test_updates_value_and_unit(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/workouts/{entry['id']}", json={"value": 10, "unit": "km"})
        assert r.status_code == 200
        data = r.json()
        assert data["value"] == 10.0
        assert data["unit"] == "km"

    def test_can_clear_value(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/workouts/{entry['id']}", json={"value": None})
        assert r.json()["value"] is None

    def test_invalid_value_returns_422(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/workouts/{entry['id']}", json={"value": "not-a-number"})
        assert r.status_code == 422

    def test_updates_notes(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/workouts/{entry['id']}", json={"notes": "Felt strong."})
        assert r.json()["notes"] == "Felt strong."

    def test_updates_logged_at(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/workouts/{entry['id']}", json={"logged_at": "2026-06-15T08:30:00"})
        assert r.status_code == 200
        assert r.json()["logged_at"].startswith("2026-06-15T08:30:00")

    def test_invalid_logged_at_returns_422(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/workouts/{entry['id']}", json={"logged_at": "not-a-date"})
        assert r.status_code == 422

    def test_partial_update_leaves_other_fields_untouched(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        client.put(f"/api/workouts/{entry['id']}", json={"value": 42})
        r = client.put(f"/api/workouts/{entry['id']}", json={"notes": "Renamed"})
        data = r.json()
        assert data["notes"] == "Renamed"
        assert data["value"] == 42.0

    def test_update_nonexistent_returns_404(self, client):
        r = client.put("/api/workouts/9999", json={"notes": "x"})
        assert r.status_code == 404


# ── Timezone handling ──────────────────────────────────────────────────────────

class TestWorkoutTimezone:

    def test_explicit_logged_at_is_stored_as_given(self, client, monkeypatch):
        """Frontend-provided local datetime is stored and filtered correctly."""
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        client.post("/api/workouts", json={"raw_input": "rowed", "logged_at": "2026-07-25T18:16:00"})
        assert len(client.get("/api/workouts?date_str=2026-07-25").json()) == 1
        assert len(client.get("/api/workouts?date_str=2026-07-26").json()) == 0

    def test_utc_offset_minutes_is_called_for_default_timestamp(self, client, monkeypatch):
        """utc_offset_minutes() is invoked when no logged_at is in the payload."""
        import daily_log
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_run)
        calls = []
        original = daily_log.utc_offset_minutes
        monkeypatch.setattr(daily_log, "utc_offset_minutes", lambda r: calls.append(r) or original(r))
        client.post("/api/workouts", json={"raw_input": "ran 3 miles"})
        assert len(calls) == 1  # offset function was called to determine local time

    def test_offset_math_produces_correct_local_date(self):
        """Offset calculation: UTC 2 AM July 26 minus 7 h = July 25 7 PM (local)."""
        from datetime import datetime, timezone, timedelta
        utc_now = datetime(2026, 7, 26, 2, 0, 0, tzinfo=timezone.utc)
        offset_mins = 420  # UTC-7 (JS convention)
        local = (utc_now - timedelta(minutes=offset_mins)).replace(tzinfo=None)
        assert local.date().isoformat() == "2026-07-25"
        assert local.hour == 19


# ── Chart endpoint ─────────────────────────────────────────────────────────────

class TestWorkoutChart:

    def test_chart_returns_all_days_in_range(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        client.post("/api/workouts", json={"raw_input": "rowed", "logged_at": "2026-07-24T10:00:00"})
        r = client.get("/api/workouts/chart?start=2026-07-23&end=2026-07-25")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 3
        dates = [d["date"] for d in data]
        assert dates == ["2026-07-23", "2026-07-24", "2026-07-25"]

    def test_chart_populated_day_has_types(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        client.post("/api/workouts", json={"raw_input": "rowed", "logged_at": "2026-07-24T10:00:00"})
        data = client.get("/api/workouts/chart?start=2026-07-23&end=2026-07-25").json()
        day24 = next(d for d in data if d["date"] == "2026-07-24")
        assert "row" in day24["types"]

    def test_chart_empty_days_have_empty_types(self, client):
        data = client.get("/api/workouts/chart?start=2026-07-23&end=2026-07-25").json()
        for day in data:
            assert day["types"] == []

    def test_chart_multiple_types_same_day(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        client.post("/api/workouts", json={"raw_input": "rowed", "logged_at": "2026-07-25T08:00:00"})
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_strength)
        client.post("/api/workouts", json={"raw_input": "lifted", "logged_at": "2026-07-25T18:00:00"})
        data = client.get("/api/workouts/chart?start=2026-07-25&end=2026-07-25").json()
        assert set(data[0]["types"]) == {"row", "strength"}


# ── Habit auto-completion ───────────────────────────────────────────────────────

class TestWorkoutHabitAutoCompletion:

    def _seed_active_experiment(self, db_session, workout_type="row"):
        habit = models.Habit(name="🧪 Row 2 miles")
        db_session.add(habit)
        db_session.commit()
        db_session.add(models.HealthExperiment(
            week=_current_isoweek(), text="t", status="active",
            workout_type=workout_type, workout_target_value=2.0, workout_unit="mi",
            habit_id=habit.id,
        ))
        db_session.commit()
        return habit

    def test_logging_a_matching_workout_checks_the_linked_habit(self, client, db_session, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        habit = self._seed_active_experiment(db_session, workout_type="row")

        r = client.post("/api/workouts", json={"raw_input": "rowed 5000m"})
        assert r.status_code == 201

        completions = db_session.query(models.HabitCompletion).filter_by(habit_id=habit.id).all()
        assert len(completions) == 1

    def test_logging_a_non_matching_workout_does_not_check_the_habit(self, client, db_session, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_run)
        habit = self._seed_active_experiment(db_session, workout_type="row")

        client.post("/api/workouts", json={"raw_input": "ran 3 miles"})

        assert db_session.query(models.HabitCompletion).filter_by(habit_id=habit.id).count() == 0

    def test_no_active_experiment_does_not_error(self, client, monkeypatch):
        monkeypatch.setattr(workouts_router, "parse_workout", _mock_parse_row)
        r = client.post("/api/workouts", json={"raw_input": "rowed 5000m"})
        assert r.status_code == 201
