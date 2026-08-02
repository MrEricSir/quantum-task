"""
Unit tests for routers/correlations.py's _load_weekly_obs.

Focused specifically on the 90-day window: cards, food entries, and workout
entries must be excluded once they fall outside the window. This used to be
filtered in Python after loading each entire table (a real efficiency
problem as those tables grow) -- these tests confirm the SQL-level filtering
produces the same, correct result.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from routers.correlations import _load_weekly_obs, _migrate_appsetting


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


TODAY = date(2026, 6, 20)
RECENT_WEEK_DATE = "2026-06-13"   # within the 90-day window
OLDER_WEEK_DATE = "2026-05-30"    # within the 90-day window, ~2 weeks earlier
OUT_OF_WINDOW_DATE = "2025-10-01"  # well over 90 days before TODAY


def _add_weight(db, date_str, value):
    db.add(models.WithingsMeasurement(date=date_str, metric="weight", value=value))


def _add_card_completed_on(db, date_str):
    db.add(models.Card(
        title="Test card", section="today", position=0,
        completed=True, completed_at=datetime.fromisoformat(f"{date_str}T10:00:00"),
    ))


def _add_food_entry_on(db, date_str, quality):
    db.add(models.FoodEntry(
        raw_input="test", name="test", category="food", meal_type="snack",
        consumed_at=datetime.fromisoformat(f"{date_str}T12:00:00"), quality=quality,
    ))


def _add_workout_on(db, date_str, workout_type):
    db.add(models.WorkoutEntry(
        raw_input="test", type=workout_type,
        logged_at=datetime.fromisoformat(f"{date_str}T08:00:00"),
    ))


class TestLoadWeeklyObsDateWindow:

    def test_cards_outside_window_are_excluded(self, db):
        _add_weight(db, RECENT_WEEK_DATE, 75.0)
        _add_weight(db, OLDER_WEEK_DATE, 76.0)
        _add_card_completed_on(db, RECENT_WEEK_DATE)
        _add_card_completed_on(db, OUT_OF_WINDOW_DATE)
        db.commit()

        weight_obs, _ = _load_weekly_obs(db, TODAY)
        assert len(weight_obs) == 1
        assert weight_obs[0]["cards_done"] == 1

    def test_food_entries_outside_window_are_excluded(self, db):
        _add_weight(db, RECENT_WEEK_DATE, 75.0)
        _add_weight(db, OLDER_WEEK_DATE, 76.0)
        _add_food_entry_on(db, RECENT_WEEK_DATE, quality=8)
        _add_food_entry_on(db, OUT_OF_WINDOW_DATE, quality=1)
        db.commit()

        weight_obs, _ = _load_weekly_obs(db, TODAY)
        assert len(weight_obs) == 1
        assert weight_obs[0]["avg_food_quality"] == 8.0

    def test_workouts_outside_window_are_excluded(self, db):
        _add_weight(db, RECENT_WEEK_DATE, 75.0)
        _add_weight(db, OLDER_WEEK_DATE, 76.0)
        _add_workout_on(db, RECENT_WEEK_DATE, "run")
        _add_workout_on(db, OUT_OF_WINDOW_DATE, "run")
        db.commit()

        weight_obs, _ = _load_weekly_obs(db, TODAY)
        assert len(weight_obs) == 1
        assert weight_obs[0]["workout_days"] == 1
        assert weight_obs[0]["cardio_days"] == 1

    def test_no_entries_produces_no_observations(self, db):
        weight_obs, fat_obs = _load_weekly_obs(db, TODAY)
        assert weight_obs == []
        assert fat_obs == []


class TestMigrateAppsetting:
    """_migrate_appsetting moves a legacy AppSetting-backed experiment into
    the HealthExperiment table. It's now called once at startup (main.py's
    _migrate_health_experiment) instead of on every GET /api/health/experiment
    request -- these tests cover the migration logic itself, which is
    unchanged, just relocated."""

    def test_returns_none_when_no_legacy_row(self, db):
        assert _migrate_appsetting(db) is None

    def test_migrates_legacy_row_into_table(self, db):
        legacy = {
            "week": "2026-W25", "text": "Try more sleep", "hypothesis": "h",
            "action": "sleep by 10pm", "needs_habit": True, "habit_id": 3,
            "withings_metric": "steps", "withings_goal": 10000,
            "created_at": "2026-06-15T08:00:00+00:00",
        }
        db.add(models.AppSetting(key="health_experiment", value=json.dumps(legacy)))
        db.commit()

        exp = _migrate_appsetting(db)

        assert exp is not None
        assert exp.week == "2026-W25"
        assert exp.text == "Try more sleep"
        assert exp.habit_id == 3
        assert exp.withings_metric == "steps"

    def test_deletes_legacy_row_after_migrating(self, db):
        db.add(models.AppSetting(key="health_experiment", value=json.dumps({"week": "2026-W25"})))
        db.commit()

        _migrate_appsetting(db)

        assert db.query(models.AppSetting).filter_by(key="health_experiment").first() is None

    def test_is_a_no_op_the_second_time(self, db):
        db.add(models.AppSetting(key="health_experiment", value=json.dumps({"week": "2026-W25"})))
        db.commit()

        first = _migrate_appsetting(db)
        second = _migrate_appsetting(db)

        assert first is not None
        assert second is None
        assert db.query(models.HealthExperiment).count() == 1

    def test_malformed_legacy_row_does_not_raise(self, db):
        db.add(models.AppSetting(key="health_experiment", value="not valid json"))
        db.commit()

        assert _migrate_appsetting(db) is None
