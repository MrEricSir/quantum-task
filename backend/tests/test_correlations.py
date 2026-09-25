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
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from routers.correlations import (
    _load_weekly_obs, _migrate_appsetting,
    _established_habits, _established_workouts, _established_foods,
    _nudge_if_near_duplicate, _generate_experiment, _record_outcome,
    _week_start, _current_isoweek, check_habit_for_workout, check_workout_for_habit,
    check_food_avoidance_habits, _recent_avg_steps,
    _routine_identity, _routine_adhered, _routine_effect, get_routine_summary,
    _compute_correlations, _compute_segments,
)
from routers.habits import check_habit_row


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
        raw_input="test", name="test", category="food",
        consumed_at=datetime.fromisoformat(f"{date_str}T12:00:00"), quality=quality,
    ))


def _add_sodium_on(db, date_str, sodium_mg):
    db.add(models.FoodEntry(
        raw_input="test", name="test", category="food",
        consumed_at=datetime.fromisoformat(f"{date_str}T12:00:00"), sodium_mg=sodium_mg,
    ))


def _add_hydration(db, date_str, value):
    db.add(models.WithingsMeasurement(date=date_str, metric="hydration", value=value))


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

        weight_obs, _, _ = _load_weekly_obs(db, TODAY)
        assert len(weight_obs) == 1
        assert weight_obs[0]["cards_done"] == 1

    def test_food_entries_outside_window_are_excluded(self, db):
        _add_weight(db, RECENT_WEEK_DATE, 75.0)
        _add_weight(db, OLDER_WEEK_DATE, 76.0)
        _add_food_entry_on(db, RECENT_WEEK_DATE, quality=8)
        _add_food_entry_on(db, OUT_OF_WINDOW_DATE, quality=1)
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, TODAY)
        assert len(weight_obs) == 1
        assert weight_obs[0]["avg_food_quality"] == 8.0

    def test_workouts_outside_window_are_excluded(self, db):
        _add_weight(db, RECENT_WEEK_DATE, 75.0)
        _add_weight(db, OLDER_WEEK_DATE, 76.0)
        _add_workout_on(db, RECENT_WEEK_DATE, "run")
        _add_workout_on(db, OUT_OF_WINDOW_DATE, "run")
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, TODAY)
        assert len(weight_obs) == 1
        assert weight_obs[0]["workout_days"] == 1
        assert weight_obs[0]["cardio_days"] == 1

    def test_no_entries_produces_no_observations(self, db):
        weight_obs, fat_obs, _ = _load_weekly_obs(db, TODAY)
        assert weight_obs == []
        assert fat_obs == []


class TestHydrationSodiumCorrelation:
    """hydration_obs (water weight) and avg_sodium -- added so the passive Correlation
    Scatter Plots feature can surface a sodium-vs-hydration relationship once enough data
    exists. Deliberately NOT wired into the active Health Experiments system -- see
    _load_weekly_obs's docstring."""

    # Five consecutive weekly Mondays -- wk0 only ever serves as the "previous" week for
    # wk1's delta, so it never gets its own row; wk1-wk4 give exactly 4 output rows, enough
    # to clear both _compute_correlations' (>=3) and _compute_segments' (>=4) thresholds.
    WK0, WK1, WK2, WK3, WK4 = (
        "2026-05-04", "2026-05-11", "2026-05-18", "2026-05-25", "2026-06-01",
    )

    def _seed(self, db):
        # Hydration trends up more in weeks with higher sodium intake that week.
        _add_hydration(db, self.WK0, 40.0)
        _add_hydration(db, self.WK1, 40.0)
        _add_sodium_on(db, self.WK1, 1000)
        _add_hydration(db, self.WK2, 40.5)
        _add_sodium_on(db, self.WK2, 2000)
        _add_hydration(db, self.WK3, 41.5)
        _add_sodium_on(db, self.WK3, 3500)
        _add_hydration(db, self.WK4, 40.0)
        _add_sodium_on(db, self.WK4, 800)
        db.commit()

    def test_load_weekly_obs_returns_hydration_as_a_third_series(self, db):
        self._seed(db)
        weight_obs, fat_obs, hydration_obs = _load_weekly_obs(db, TODAY)
        assert weight_obs == []
        assert fat_obs == []
        assert len(hydration_obs) == 4

    def test_avg_sodium_is_attached_to_every_outcome_series(self, db):
        _add_weight(db, self.WK0, 75.0)
        _add_weight(db, self.WK1, 75.0)
        _add_sodium_on(db, self.WK1, 1234)
        db.commit()
        weight_obs, _, _ = _load_weekly_obs(db, TODAY)
        assert weight_obs[0]["avg_sodium"] == 1234.0

    def test_compute_correlations_finds_sodium_hydration_relationship(self, db):
        self._seed(db)
        _, _, hydration_obs = _load_weekly_obs(db, TODAY)
        correlations = _compute_correlations([], [], hydration_obs)
        sodium_hydration = next(
            c for c in correlations
            if c["factor"] == "Daily sodium" and c["outcome"] == "Water weight change"
        )
        assert sodium_hydration["n"] == 4
        assert sodium_hydration["r"] > 0.5

    def test_compute_correlations_omits_hydration_outcome_by_default(self, db):
        """The active Health Experiments call site relies on this default -- passing no
        hydration_obs must never surface a "Water weight change" outcome."""
        self._seed(db)
        weight_obs, fat_obs, _ = _load_weekly_obs(db, TODAY)
        correlations = _compute_correlations(weight_obs, fat_obs)
        assert not any(c["outcome"] == "Water weight change" for c in correlations)

    def test_compute_segments_includes_hydration_when_passed(self, db):
        self._seed(db)
        _, _, hydration_obs = _load_weekly_obs(db, TODAY)
        segments = _compute_segments([], [], hydration_obs)
        assert any(
            s["factor"] == "Daily sodium" and s["outcome"] == "Water weight change"
            for s in segments
        )


class TestLoadWeeklyObsTimezone:

    def test_cards_done_bucketed_by_local_date_not_raw_utc(self, db):
        """Regression test: cards_done_by_date used to key on
        card.completed_at.strftime(...) with no offset conversion -- completed_at is a
        UTC instant. 2026-06-14T22:00:00 UTC (Sunday, same ISO week as RECENT_WEEK_DATE)
        is 2026-06-15T08:00:00 local for a client 10 hours ahead of UTC (offset=-600,
        e.g. Sydney) -- Monday, the FOLLOWING ISO week. The card must not be credited to
        RECENT_WEEK_DATE's week just because its raw UTC date happens to still fall in it."""
        _add_weight(db, RECENT_WEEK_DATE, 75.0)
        _add_weight(db, OLDER_WEEK_DATE, 76.0)
        db.add(models.Card(
            title="Test card", section="today", position=0,
            completed=True, completed_at=datetime.fromisoformat("2026-06-14T22:00:00"),
        ))
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, TODAY, tz_offset_minutes=-600)
        assert len(weight_obs) == 1
        assert weight_obs[0]["cards_done"] is None


class TestLoadWeeklyObsTripDays:
    """Trip days affect different _load_weekly_obs signals differently -- see that
    function's own docstring for the full reasoning. Weight/fat_ratio (the outcome) and
    habit completions skip trip days; steps/food/workout data (confound signals) don't."""

    def _weeks(self):
        recent_ws = _week_start(_current_isoweek(TODAY)) - timedelta(days=7)
        older_ws = recent_ws - timedelta(days=7)
        return older_ws, recent_ws

    def test_weight_on_a_trip_day_is_excluded_from_that_weeks_average(self, db):
        older_ws, recent_ws = self._weeks()
        _add_weight(db, older_ws.isoformat(), 70.0)
        _add_weight(db, recent_ws.isoformat(), 70.0)
        trip_day = recent_ws + timedelta(days=2)
        _add_weight(db, trip_day.isoformat(), 100.0)  # would badly skew the average if counted
        db.add(models.Trip(start_date=trip_day.isoformat(), end_date=trip_day.isoformat()))
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, TODAY)

        assert len(weight_obs) == 1
        # If the trip-day 100.0 reading were included, delta_per_day would be nonzero.
        assert abs(weight_obs[0]["delta_per_day"]) < 1e-9

    def test_habit_completion_on_a_trip_day_is_excluded_like_a_streak_day(self, db):
        older_ws, recent_ws = self._weeks()
        _add_weight(db, older_ws.isoformat(), 70.0)
        _add_weight(db, recent_ws.isoformat(), 70.0)
        habit = models.Habit(name="Meditate")
        db.add(habit)
        db.flush()
        trip_day = recent_ws + timedelta(days=2)
        db.add(models.HabitCompletion(habit_id=habit.id, date=recent_ws.isoformat()))
        db.add(models.HabitCompletion(habit_id=habit.id, date=trip_day.isoformat()))
        db.add(models.Trip(start_date=trip_day.isoformat(), end_date=trip_day.isoformat()))
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, TODAY)

        # 1 real completion out of (7 - 1 trip day) = 6 eligible days, not 2 out of 7 --
        # the trip-day completion itself doesn't count, and the denominator shrinks too.
        assert abs(weight_obs[0]["habit_rate"] - (1 / 6)) < 1e-9

    def test_steps_on_a_trip_day_are_not_excluded(self, db):
        """Steps is a confound signal (_CONFOUND_VARS), not the outcome -- a travel-driven
        step spike should stay visible so the confound check can actually catch it, not get
        silently dropped."""
        older_ws, recent_ws = self._weeks()
        _add_weight(db, older_ws.isoformat(), 70.0)
        _add_weight(db, recent_ws.isoformat(), 70.0)
        trip_day = recent_ws + timedelta(days=2)
        db.add(models.WithingsMeasurement(date=trip_day.isoformat(), metric="steps", value=20000))
        db.add(models.Trip(start_date=trip_day.isoformat(), end_date=trip_day.isoformat()))
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, TODAY)

        assert weight_obs[0]["avg_steps"] == 20000

    def test_food_quality_on_a_trip_day_is_not_excluded(self, db):
        older_ws, recent_ws = self._weeks()
        _add_weight(db, older_ws.isoformat(), 70.0)
        _add_weight(db, recent_ws.isoformat(), 70.0)
        trip_day = recent_ws + timedelta(days=2)
        _add_food_entry_on(db, trip_day.isoformat(), quality=9)
        db.add(models.Trip(start_date=trip_day.isoformat(), end_date=trip_day.isoformat()))
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, TODAY)

        assert weight_obs[0]["avg_food_quality"] == 9

    def test_workout_on_a_trip_day_is_not_excluded(self, db):
        older_ws, recent_ws = self._weeks()
        _add_weight(db, older_ws.isoformat(), 70.0)
        _add_weight(db, recent_ws.isoformat(), 70.0)
        trip_day = recent_ws + timedelta(days=2)
        _add_workout_on(db, trip_day.isoformat(), "row")
        db.add(models.Trip(start_date=trip_day.isoformat(), end_date=trip_day.isoformat()))
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, TODAY)

        assert weight_obs[0]["workout_days"] == 1


class TestMigrateAppsetting:
    """_migrate_appsetting moves a legacy AppSetting-backed experiment into
    the HealthExperiment table. It's now called once at startup (main.py's
    _migrate_health_experiment) instead of on every GET /api/health/experiment
    request -- these tests cover the migration logic itself, which is
    unchanged, just relocated."""

    def test_returns_none_when_no_legacy_row(self, db):
        assert _migrate_appsetting(db) is None

    def test_migrates_legacy_row_into_table(self, db):
        # Deliberately still the OLD key names -- this simulates a JSON blob
        # written before the health_metric/health_goal rename, which
        # _migrate_appsetting must keep reading correctly regardless of what
        # the live model calls the field now.
        legacy = {
            "week": "2026-W25", "text": "Try more sleep", "hypothesis": "h",
            "action": "sleep by 10pm", "habit_id": 3,
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
        assert exp.health_metric == "steps"

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


# ── Existing-routine detection ──────────────────────────────────────────────────

def _add_habit_completions(db, habit_id, today, n_days, out_of=21):
    for i in range(n_days):
        db.add(models.HabitCompletion(habit_id=habit_id, date=(today - timedelta(days=i)).isoformat()))


def _add_workout(db, workout_type, value, unit, days_ago):
    db.add(models.WorkoutEntry(
        raw_input="test", type=workout_type, value=value, unit=unit,
        logged_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    ))


def _add_food_named(db, name, days_ago):
    db.add(models.FoodEntry(
        raw_input=name, name=name, category="food",
        consumed_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    ))


class TestEstablishedHabits:

    def test_includes_habit_meeting_age_and_completion_thresholds(self, db):
        today = date.today()
        habit = models.Habit(name="Meditate 10 min", created_at=datetime.now(timezone.utc) - timedelta(days=30))
        db.add(habit)
        db.commit()
        _add_habit_completions(db, habit.id, today, n_days=15)  # 15/21 >= 60%
        db.commit()

        result = _established_habits(db, today)
        assert len(result) == 1
        assert result[0]["name"] == "Meditate 10 min"
        assert result[0]["completion_rate"] >= 0.6

    def test_excludes_habit_younger_than_min_age(self, db):
        today = date.today()
        habit = models.Habit(name="New habit", created_at=datetime.now(timezone.utc) - timedelta(days=5))
        db.add(habit)
        db.commit()
        _add_habit_completions(db, habit.id, today, n_days=5)
        db.commit()

        assert _established_habits(db, today) == []

    def test_excludes_habit_below_completion_threshold(self, db):
        today = date.today()
        habit = models.Habit(name="Rarely done", created_at=datetime.now(timezone.utc) - timedelta(days=30))
        db.add(habit)
        db.commit()
        db.add(models.HabitCompletion(habit_id=habit.id, date=today.isoformat()))  # 1/21
        db.commit()

        assert _established_habits(db, today) == []

    def test_excludes_archived_habits(self, db):
        today = date.today()
        habit = models.Habit(
            name="Archived", created_at=datetime.now(timezone.utc) - timedelta(days=30), archived=True,
        )
        db.add(habit)
        db.commit()
        _add_habit_completions(db, habit.id, today, n_days=21)
        db.commit()

        assert _established_habits(db, today) == []

    def test_excludes_health_metric_linked_habits(self, db):
        today = date.today()
        habit = models.Habit(
            name="Steps", created_at=datetime.now(timezone.utc) - timedelta(days=30),
            health_metric="steps", health_goal=8000,
        )
        db.add(habit)
        db.commit()
        _add_habit_completions(db, habit.id, today, n_days=21)
        db.commit()

        assert _established_habits(db, today) == []


class TestEstablishedHabitsTripDays:

    def test_a_trip_does_not_make_an_otherwise_consistent_habit_look_unestablished(self, db):
        """10 completions out of 21 raw days (~48%) would fall below the 60% threshold and
        wrongly exclude an otherwise-perfect habit -- but 7 of those 21 days were a trip, so
        the real eligible window is 14 days, and 10/14 (~71%) correctly clears the bar."""
        today = date(2026, 6, 20)
        habit = models.Habit(name="Meditate 10 min", created_at=datetime(2026, 5, 1))
        db.add(habit)
        db.flush()
        trip_start = today - timedelta(days=10)
        trip_end = today - timedelta(days=4)  # days_ago 4..10, 7 days
        db.add(models.Trip(start_date=trip_start.isoformat(), end_date=trip_end.isoformat()))
        # 10 completions on non-trip days within the 21-day window (days_ago 0-2, 11-17).
        for days_ago in list(range(0, 3)) + list(range(11, 18)):
            db.add(models.HabitCompletion(habit_id=habit.id, date=(today - timedelta(days=days_ago)).isoformat()))
        db.commit()

        result = _established_habits(db, today)

        assert len(result) == 1
        assert abs(result[0]["completion_rate"] - (10 / 14)) < 0.01


class TestEstablishedHabitsTimezone:

    def test_habit_age_uses_local_created_date_not_raw_utc(self, db):
        """Regression test: `created = h.created_at.date()` used to read the SERVER's
        UTC date directly, with no offset conversion. 2026-08-05T15:00:00 UTC is
        2026-08-06 01:00 local for a client 10 hours ahead of UTC (offset=-600, e.g.
        Sydney) -- one day YOUNGER than the raw UTC date suggests, which is exactly
        enough to flip whether a 14-day-min-age habit created on Aug 5 (UTC) already
        qualifies as established when local "today" is Aug 19."""
        today = date(2026, 8, 19)
        habit = models.Habit(name="Borderline habit", created_at=datetime(2026, 8, 5, 15, 0))
        db.add(habit)
        db.commit()
        _add_habit_completions(db, habit.id, today, n_days=15)
        db.commit()

        assert _established_habits(db, today, tz_offset_minutes=-600) == []


class TestEstablishedWorkouts:

    def test_includes_type_meeting_session_threshold(self, db):
        for i in range(4):
            _add_workout(db, "row", 1.5, "mi", days_ago=i * 7)
        db.commit()

        result = _established_workouts(db, date.today())
        assert len(result) == 1
        assert result[0]["type"] == "row"
        assert result[0]["avg_value"] == 1.5
        assert result[0]["unit"] == "mi"

    def test_excludes_type_below_session_threshold(self, db):
        for i in range(2):
            _add_workout(db, "swim", 500, "m", days_ago=i * 7)
        db.commit()

        assert _established_workouts(db, date.today()) == []

    def test_excludes_entries_outside_window(self, db):
        for i in range(4):
            _add_workout(db, "row", 1.5, "mi", days_ago=100)
        db.commit()

        assert _established_workouts(db, date.today()) == []

    def test_picks_most_common_unit(self, db):
        _add_workout(db, "row", 1.0, "mi", days_ago=0)
        _add_workout(db, "row", 1.5, "mi", days_ago=7)
        _add_workout(db, "row", 2000, "m", days_ago=14)
        db.commit()

        result = _established_workouts(db, date.today())
        assert result[0]["unit"] == "mi"

    def test_a_trip_does_not_understate_sessions_per_week(self, db):
        """5 sessions over the full 42-day window would be ~0.83/week, but 7 of those days
        were a trip -- the real eligible window is 35 days (5 weeks), so 5 sessions is
        really a clean 1x/week, not diluted by days there was no reasonable chance to log one."""
        today = date.today()
        trip_start = today - timedelta(days=16)
        trip_end = today - timedelta(days=10)  # 7-day trip, days_ago 10-16
        db.add(models.Trip(start_date=trip_start.isoformat(), end_date=trip_end.isoformat()))
        for days_ago in [0, 7, 21, 28, 35]:
            _add_workout(db, "row", 1.5, "mi", days_ago=days_ago)
        db.commit()

        result = _established_workouts(db, today)

        assert len(result) == 1
        assert result[0]["sessions_per_week"] == 1.0


class TestEstablishedFoods:

    def test_includes_food_meeting_day_threshold(self, db):
        for i in range(4):
            _add_food_named(db, "coffee", days_ago=i * 5)  # 4 distinct days within 21
        db.commit()

        result = _established_foods(db, date.today())
        assert len(result) == 1
        assert result[0]["name"] == "coffee"
        assert result[0]["days_per_week"] > 0

    def test_excludes_food_below_day_threshold(self, db):
        for i in range(2):
            _add_food_named(db, "pizza", days_ago=i * 5)
        db.commit()

        assert _established_foods(db, date.today()) == []

    def test_counts_distinct_days_not_raw_entries(self, db):
        # 5 entries all on the SAME day -- 1 distinct day, below the 4-day
        # threshold even though the raw entry count (5) would clear it.
        for _ in range(5):
            _add_food_named(db, "coffee", days_ago=0)
        db.commit()

        assert _established_foods(db, date.today()) == []

    def test_excludes_entries_outside_window(self, db):
        for i in range(4):
            _add_food_named(db, "coffee", days_ago=30 + i)
        db.commit()

        assert _established_foods(db, date.today()) == []

    def test_matches_case_insensitively(self, db):
        _add_food_named(db, "Coffee", days_ago=0)
        _add_food_named(db, "coffee", days_ago=5)
        _add_food_named(db, "COFFEE", days_ago=10)
        _add_food_named(db, "cOffee", days_ago=15)
        db.commit()

        result = _established_foods(db, date.today())
        assert len(result) == 1
        assert result[0]["name"] == "coffee"

    def test_sorted_by_frequency_descending_and_capped_at_8(self, db):
        for n in range(10):
            name = f"food{n}"
            # food0 gets 11 occurrences (most frequent), food9 gets 4 (least, still established)
            for i in range(4 + (10 - n)):
                _add_food_named(db, name, days_ago=i)
        db.commit()

        result = _established_foods(db, date.today())
        assert len(result) == 8
        assert result[0]["name"] == "food0"
        assert all(result[i]["days_per_week"] >= result[i + 1]["days_per_week"] for i in range(len(result) - 1))

    def test_a_trip_does_not_understate_days_per_week(self, db):
        today = date.today()
        trip_start = today - timedelta(days=16)
        trip_end = today - timedelta(days=10)  # 7-day trip, days_ago 10-16
        db.add(models.Trip(start_date=trip_start.isoformat(), end_date=trip_end.isoformat()))
        for days_ago in [0, 5, 19, 20]:
            _add_food_named(db, "coffee", days_ago=days_ago)
        db.commit()

        result = _established_foods(db, today)

        assert len(result) == 1
        # eligible_weeks = (21 - 7) / 7 = 2; 4 distinct days / 2 weeks = 2.0/week
        assert result[0]["days_per_week"] == 2.0


class TestNudgeIfNearDuplicate:

    def test_returns_unchanged_when_no_prior_experiment(self):
        goal, workout_target = _nudge_if_near_duplicate("steps", 8000, None, None, None)
        assert (goal, workout_target) == (8000, None)

    def test_nudges_identical_health_goal(self):
        prev = models.HealthExperiment(week="2020-W01", text="t", health_metric="steps", health_goal=8000.0)
        goal, _ = _nudge_if_near_duplicate("steps", 8000, None, None, prev)
        assert goal == 9600

    def test_leaves_meaningfully_different_health_goal_unchanged(self):
        prev = models.HealthExperiment(week="2020-W01", text="t", health_metric="steps", health_goal=8000.0)
        goal, _ = _nudge_if_near_duplicate("steps", 10000, None, None, prev)
        assert goal == 10000

    def test_leaves_different_metric_unchanged(self):
        prev = models.HealthExperiment(week="2020-W01", text="t", health_metric="weight", health_goal=75.0)
        goal, _ = _nudge_if_near_duplicate("steps", 8000, None, None, prev)
        assert goal == 8000

    def test_nudges_identical_workout_target(self):
        prev = models.HealthExperiment(week="2020-W01", text="t", workout_type="row", workout_target_value=2.0)
        _, target = _nudge_if_near_duplicate(None, None, "row", 2.0, prev)
        assert target == 2.4

    def test_leaves_different_workout_type_unchanged(self):
        prev = models.HealthExperiment(week="2020-W01", text="t", workout_type="run", workout_target_value=3.0)
        _, target = _nudge_if_near_duplicate(None, None, "row", 2.0, prev)
        assert target == 2.0


class TestRecordOutcomeWorkout:

    def test_computes_baseline_and_experiment_averages_and_pvalue(self, db):
        scratch_week = "2026-W10"
        ws = _week_start(scratch_week)
        for i, v in enumerate([1.0, 1.1, 0.9, 1.0]):
            db.add(models.WorkoutEntry(
                raw_input="row", type="row", value=v, unit="mi",
                logged_at=datetime.combine(ws - timedelta(days=14 + i), datetime.min.time()),
            ))
        for i, v in enumerate([2.0, 1.9, 2.1, 2.0]):
            db.add(models.WorkoutEntry(
                raw_input="row", type="row", value=v, unit="mi",
                logged_at=datetime.combine(ws + timedelta(days=i), datetime.min.time()),
            ))
        exp = models.HealthExperiment(
            week=scratch_week, text="t", workout_type="row",
            workout_target_value=2.0, workout_unit="mi",
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, date.today())

        assert exp.workout_baseline_n == 4
        assert exp.workout_experiment_n == 4
        assert abs(exp.workout_baseline_avg - 1.0) < 0.05
        assert abs(exp.workout_experiment_avg - 2.0) < 0.05
        assert exp.workout_p is not None
        assert exp.workout_p < 0.05

    def test_leaves_workout_fields_none_when_no_workout_type(self, db):
        exp = models.HealthExperiment(week="2026-W10", text="t")
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, date.today())

        assert exp.workout_baseline_avg is None
        assert exp.workout_p is None

    def test_p_stays_none_with_insufficient_samples(self, db):
        scratch_week = "2026-W11"
        ws = _week_start(scratch_week)
        db.add(models.WorkoutEntry(
            raw_input="row", type="row", value=2.0, unit="mi",
            logged_at=datetime.combine(ws, datetime.min.time()),
        ))
        exp = models.HealthExperiment(
            week=scratch_week, text="t", workout_type="row",
            workout_target_value=2.0, workout_unit="mi",
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, date.today())

        assert exp.workout_experiment_n == 1
        assert exp.workout_p is None

    def test_baseline_uses_only_weeks_workout_was_actually_present(self, db):
        # Same setup shape as TestRecordOutcomeFood's equivalent test -- 5
        # consecutive weekly weight readings, workout logged only in the
        # weeks behind readings[1] and readings[3].
        base = date(2026, 3, 2)
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            _add_weight(db, (base + timedelta(days=7 * i)).isoformat(), w)
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, base + timedelta(days=35))
        assert len(weight_obs) == 4
        exp_week = weight_obs[-1]["date"]

        for offset in (7, 21):
            db.add(models.WorkoutEntry(
                raw_input="row", type="row", value=1.5, unit="mi",
                logged_at=datetime.combine(base + timedelta(days=offset), datetime.min.time()),
            ))
        db.commit()

        exp = models.HealthExperiment(
            week=exp_week, text="t", workout_type="row",
            workout_target_value=2.0, workout_unit="mi",
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, base + timedelta(days=35))

        assert exp.workout_baseline_weeks_n == 2
        expected = (weight_obs[0]["delta_per_day"] + weight_obs[2]["delta_per_day"]) / 2
        assert abs(exp.weight_baseline - expected) < 1e-6

    def test_falls_back_to_generic_baseline_with_fewer_than_2_present_weeks(self, db):
        base = date(2026, 3, 2)
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            _add_weight(db, (base + timedelta(days=7 * i)).isoformat(), w)
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, base + timedelta(days=35))
        exp_week = weight_obs[-1]["date"]

        db.add(models.WorkoutEntry(
            raw_input="row", type="row", value=1.5, unit="mi",
            logged_at=datetime.combine(base + timedelta(days=7), datetime.min.time()),
        ))
        db.commit()

        exp = models.HealthExperiment(
            week=exp_week, text="t", workout_type="row",
            workout_target_value=2.0, workout_unit="mi",
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, base + timedelta(days=35))

        assert exp.workout_baseline_weeks_n is None
        generic = sum(r["delta_per_day"] for r in weight_obs[:-1]) / 3
        assert abs(exp.weight_baseline - generic) < 1e-6

    def test_confound_check_compares_average_calories(self, db):
        base = date(2026, 3, 2)
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            _add_weight(db, (base + timedelta(days=7 * i)).isoformat(), w)
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, base + timedelta(days=35))
        exp_week = weight_obs[-1]["date"]

        for offset, cals in ((7, 2000), (21, 2200)):
            d = base + timedelta(days=offset)
            db.add(models.WorkoutEntry(raw_input="row", type="row", value=1.5, unit="mi",
                                        logged_at=datetime.combine(d, datetime.min.time())))
            db.add(models.FoodEntry(raw_input="lunch", name="Lunch", category="food", calories=cals,
                                     consumed_at=datetime.combine(d, datetime.min.time())))
        exp_day = base + timedelta(days=28)
        db.add(models.FoodEntry(raw_input="lunch", name="Lunch", category="food", calories=1500,
                                 consumed_at=datetime.combine(exp_day, datetime.min.time())))
        db.commit()

        exp = models.HealthExperiment(
            week=exp_week, text="t", workout_type="row",
            workout_target_value=2.0, workout_unit="mi",
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, base + timedelta(days=35))

        confounds = json.loads(exp.confounds)
        assert confounds["avg_calories"] == {"baseline": 2100.0, "experiment": 1500.0}

    def test_confound_check_also_compares_average_steps(self, db):
        """Steps was tracked all along but never checked as a confound for any experiment
        type -- only calories was. Same matched-baseline weeks as the calorie test above,
        just a different rival variable."""
        base = date(2026, 3, 2)
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            _add_weight(db, (base + timedelta(days=7 * i)).isoformat(), w)
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, base + timedelta(days=35))
        exp_week = weight_obs[-1]["date"]

        for offset, steps in ((7, 6000), (21, 6400)):
            d = base + timedelta(days=offset)
            db.add(models.WorkoutEntry(raw_input="row", type="row", value=1.5, unit="mi",
                                        logged_at=datetime.combine(d, datetime.min.time())))
            db.add(models.WithingsMeasurement(date=d.isoformat(), metric="steps", value=steps))
        exp_day = base + timedelta(days=28)
        db.add(models.WithingsMeasurement(date=exp_day.isoformat(), metric="steps", value=18000))
        db.commit()

        exp = models.HealthExperiment(
            week=exp_week, text="t", workout_type="row",
            workout_target_value=2.0, workout_unit="mi",
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, base + timedelta(days=35))

        confounds = json.loads(exp.confounds)
        assert confounds["avg_steps"] == {"baseline": 6200.0, "experiment": 18000.0}


class TestRecordOutcomeHabit:
    """Habit-backed experiments (e.g. "sleep 8 hours") previously got no confound check at
    all -- no matched baseline (impossible anyway: the tracking habit is always freshly
    created for that one week, with no prior history of its own to match against) AND no
    confound check against the generic all-other-weeks baseline either. Now they get the
    same confound check food/workout experiments do, just against that generic baseline."""

    def test_confound_check_runs_against_the_generic_baseline(self, db):
        base = date(2026, 3, 2)
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            d = base + timedelta(days=7 * i)
            _add_weight(db, d.isoformat(), w)
            db.add(models.WithingsMeasurement(
                date=d.isoformat(), metric="steps", value=6000.0 if i < 4 else 18000.0,
            ))
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, base + timedelta(days=35))
        exp_week = weight_obs[-1]["date"]

        exp = models.HealthExperiment(week=exp_week, text="t")
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, base + timedelta(days=35))

        confounds = json.loads(exp.confounds)
        assert confounds["avg_steps"] == {"baseline": 6000.0, "experiment": 18000.0}

    def test_confounds_is_none_when_theres_no_data_to_compare(self, db):
        exp = models.HealthExperiment(week="2026-W13", text="t")
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, date.today())

        assert exp.confounds is None


class TestRecordOutcomeHabitCompletionRateTripDays:

    def test_trip_days_are_excluded_from_both_sides_of_the_rate(self, db):
        week = "2026-W10"
        ws = _week_start(week)
        habit = models.Habit(name="Meditate")
        db.add(habit)
        db.flush()
        exp = models.HealthExperiment(week=week, text="t", habit_id=habit.id)
        db.add(exp)
        trip_start = ws + timedelta(days=3)
        trip_end = ws + timedelta(days=4)
        db.add(models.Trip(start_date=trip_start.isoformat(), end_date=trip_end.isoformat()))
        # Completed on days 0, 1, 2 (non-trip) -- 3 out of (7 - 2) = 5 eligible days.
        for i in [0, 1, 2]:
            db.add(models.HabitCompletion(habit_id=habit.id, date=(ws + timedelta(days=i)).isoformat()))
        db.commit()

        _record_outcome(exp, db, ws + timedelta(days=10))

        assert abs(exp.habit_completion_rate - (3 / 5)) < 1e-9

    def test_a_completion_during_a_trip_does_not_push_the_rate_above_one(self, db):
        week = "2026-W10"
        ws = _week_start(week)
        habit = models.Habit(name="Meditate")
        db.add(habit)
        db.flush()
        exp = models.HealthExperiment(week=week, text="t", habit_id=habit.id)
        db.add(exp)
        trip_day = ws + timedelta(days=3)
        db.add(models.Trip(start_date=trip_day.isoformat(), end_date=trip_day.isoformat()))
        for i in range(7):
            db.add(models.HabitCompletion(habit_id=habit.id, date=(ws + timedelta(days=i)).isoformat()))
        db.commit()

        _record_outcome(exp, db, ws + timedelta(days=10))

        assert exp.habit_completion_rate == 1.0


class TestRoutineIdentity:

    def test_metric_identity(self):
        exp = models.HealthExperiment(week="2026-W10", text="t", health_metric="steps")
        assert _routine_identity(exp) == ("metric", "steps")

    def test_workout_identity(self):
        exp = models.HealthExperiment(week="2026-W10", text="t", workout_type="row")
        assert _routine_identity(exp) == ("workout", "row")

    def test_food_identity(self):
        exp = models.HealthExperiment(week="2026-W10", text="t", food_name="coffee")
        assert _routine_identity(exp) == ("food", "coffee")

    def test_habit_routine_has_no_identity(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t", action="screen-free time", routine_type="habit",
        )
        assert _routine_identity(exp) is None

    def test_no_fields_set_has_no_identity(self):
        exp = models.HealthExperiment(week="2026-W10", text="t")
        assert _routine_identity(exp) is None


class TestRoutineAdhered:

    def test_food_adhered_when_cut_to_half_or_less(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t", food_name="coffee",
            food_baseline_frequency=4.0, food_experiment_count=1,
        )
        assert _routine_adhered(exp) is True

    def test_food_not_adhered_when_still_at_usual_frequency(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t", food_name="coffee",
            food_baseline_frequency=4.0, food_experiment_count=4,
        )
        assert _routine_adhered(exp) is False

    def test_food_unknown_when_missing_data(self):
        exp = models.HealthExperiment(week="2026-W10", text="t", food_name="coffee")
        assert _routine_adhered(exp) is None

    def test_workout_adhered_via_significant_pvalue(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t", workout_type="row",
            workout_baseline_avg=1.0, workout_experiment_avg=1.05, workout_p=0.01,
        )
        assert _routine_adhered(exp) is True

    def test_workout_adhered_via_target_met(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t", workout_type="row",
            workout_baseline_avg=1.0, workout_experiment_avg=2.0,
            workout_target_value=2.0, workout_p=0.5,
        )
        assert _routine_adhered(exp) is True

    def test_workout_not_adhered_when_neither_condition_met(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t", workout_type="row",
            workout_baseline_avg=1.0, workout_experiment_avg=1.1,
            workout_target_value=2.0, workout_p=0.5,
        )
        assert _routine_adhered(exp) is False

    def test_workout_unknown_when_missing_data(self):
        exp = models.HealthExperiment(week="2026-W10", text="t", workout_type="row")
        assert _routine_adhered(exp) is None

    def test_metric_adhered_via_habit_completion_rate(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t", health_metric="steps", habit_completion_rate=0.7,
        )
        assert _routine_adhered(exp) is True

    def test_metric_not_adhered_below_half(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t", health_metric="steps", habit_completion_rate=0.3,
        )
        assert _routine_adhered(exp) is False

    def test_metric_unknown_when_no_habit(self):
        exp = models.HealthExperiment(week="2026-W10", text="t", health_metric="steps")
        assert _routine_adhered(exp) is None


class TestRoutineEffect:

    def test_averages_weight_and_fat_when_both_present(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t",
            weight_delta=-0.05, weight_baseline=-0.01,
            fat_delta=-0.03, fat_baseline=-0.01,
        )
        assert abs(_routine_effect(exp) - ((-0.04 + -0.02) / 2)) < 1e-9

    def test_uses_weight_only_when_no_fat_data(self):
        exp = models.HealthExperiment(
            week="2026-W10", text="t", weight_delta=-0.05, weight_baseline=-0.01,
        )
        assert abs(_routine_effect(exp) - -0.04) < 1e-9

    def test_none_when_neither_present(self):
        exp = models.HealthExperiment(week="2026-W10", text="t")
        assert _routine_effect(exp) is None


class TestGetRoutineSummary:

    def _dismissed(self, db, **kwargs):
        defaults = dict(week="2026-W10", text="t", status="dismissed")
        defaults.update(kwargs)
        exp = models.HealthExperiment(**defaults)
        db.add(exp)
        db.commit()
        return exp

    def test_groups_repeated_attempts_at_the_same_routine(self, db):
        self._dismissed(
            db, workout_type="row", workout_baseline_avg=1.0, workout_experiment_avg=2.0,
            workout_target_value=2.0, workout_p=0.01,
            weight_delta=-0.05, weight_baseline=-0.01,
        )
        self._dismissed(
            db, week="2026-W15", workout_type="row",
            workout_baseline_avg=1.0, workout_experiment_avg=2.0,
            workout_target_value=2.0, workout_p=0.01,
            weight_delta=-0.06, weight_baseline=-0.01,
        )
        results = get_routine_summary(db)
        assert len(results) == 1
        assert results[0]["category"] == "workout"
        assert results[0]["label"] == "row"
        assert results[0]["n"] == 2
        assert results[0]["verdict"] == "positive"
        assert results[0]["better_weeks"] == 2

    def test_excludes_non_adhered_experiments(self, db):
        self._dismissed(
            db, food_name="coffee", food_baseline_frequency=4.0, food_experiment_count=4,
            weight_delta=-0.05, weight_baseline=-0.01,
        )
        assert get_routine_summary(db) == []

    def test_excludes_active_experiments(self, db):
        self._dismissed(
            db, status="active", workout_type="row",
            workout_baseline_avg=1.0, workout_experiment_avg=2.0,
            workout_target_value=2.0, workout_p=0.01,
            weight_delta=-0.05, weight_baseline=-0.01,
        )
        assert get_routine_summary(db) == []

    def test_excludes_habit_routine_experiments(self, db):
        self._dismissed(
            db, action="screen-free time", routine_type="habit",
            habit_completion_rate=1.0,
            weight_delta=-0.05, weight_baseline=-0.01,
        )
        assert get_routine_summary(db) == []

    def test_negative_verdict_when_effect_is_worse(self, db):
        self._dismissed(
            db, food_name="coffee", food_baseline_frequency=4.0, food_experiment_count=0,
            weight_delta=0.05, weight_baseline=-0.01,
        )
        results = get_routine_summary(db)
        assert results[0]["verdict"] == "negative"
        assert results[0]["worse_weeks"] == 1

    def test_mixed_verdict_when_effects_disagree(self, db):
        self._dismissed(
            db, food_name="coffee", food_baseline_frequency=4.0, food_experiment_count=0,
            weight_delta=-0.05, weight_baseline=-0.01,
        )
        self._dismissed(
            db, week="2026-W15", food_name="coffee",
            food_baseline_frequency=4.0, food_experiment_count=0,
            weight_delta=0.05, weight_baseline=-0.01,
        )
        results = get_routine_summary(db)
        assert results[0]["verdict"] == "mixed"
        assert results[0]["n"] == 2

    def test_inconclusive_when_no_clear_effect(self, db):
        self._dismissed(
            db, food_name="coffee", food_baseline_frequency=4.0, food_experiment_count=0,
            weight_delta=0.0, weight_baseline=0.0,
        )
        results = get_routine_summary(db)
        assert results[0]["verdict"] == "inconclusive"

    def test_positive_routines_sort_before_negative(self, db):
        self._dismissed(
            db, food_name="coffee", food_baseline_frequency=4.0, food_experiment_count=0,
            weight_delta=0.05, weight_baseline=-0.01,
        )
        self._dismissed(
            db, week="2026-W15", workout_type="row",
            workout_baseline_avg=1.0, workout_experiment_avg=2.0,
            workout_target_value=2.0, workout_p=0.01,
            weight_delta=-0.05, weight_baseline=-0.01,
        )
        results = get_routine_summary(db)
        assert [r["verdict"] for r in results] == ["positive", "negative"]


class TestRecomputeExperimentOutcomes:
    """POST /api/health/experiments/recompute -- re-runs _record_outcome for every
    already-completed experiment, so the habit/steps confound check (and any future analysis
    improvement) applies retroactively instead of only to experiments that haven't run yet."""

    def test_recomputes_every_non_active_experiment_and_returns_the_count(self, db):
        db.add(models.HealthExperiment(week="2026-W05", text="t", status="dismissed"))
        db.add(models.HealthExperiment(week="2026-W06", text="t", status="dismissed"))
        db.add(models.HealthExperiment(week="2026-W07", text="t", status="active"))
        db.commit()

        from routers.correlations import recompute_experiment_outcomes
        result = recompute_experiment_outcomes(db=db)

        assert result == {"recomputed": 2}

    def test_anchors_to_the_experiments_own_week_not_today(self, db):
        """The bug this guards against: recomputing an old experiment against
        date.today() would push its own week outside _load_weekly_obs's 90-day trailing
        window and silently blank weight_delta/confounds instead of fixing them."""
        old_base = date(2026, 1, 5)  # far enough back that date.today() in these tests
                                       # (anchored around 2026-06-20 elsewhere in this file)
                                       # would put it well outside a 90-day trailing window
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            d = old_base + timedelta(days=7 * i)
            _add_weight(db, d.isoformat(), w)
            db.add(models.WithingsMeasurement(
                date=d.isoformat(), metric="steps", value=6000.0 if i < 4 else 18000.0,
            ))
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, old_base + timedelta(days=35))
        exp_week = weight_obs[-1]["date"]
        exp = models.HealthExperiment(week=exp_week, text="t", status="dismissed")
        db.add(exp)
        db.commit()

        from routers.correlations import recompute_experiment_outcomes
        recompute_experiment_outcomes(db=db)
        db.refresh(exp)

        assert exp.weight_delta is not None
        assert exp.confounds is not None
        confounds = json.loads(exp.confounds)
        assert confounds["avg_steps"] == {"baseline": 6000.0, "experiment": 18000.0}

    def test_active_experiments_are_left_untouched(self, db):
        exp = models.HealthExperiment(week="2026-W07", text="t", status="active")
        db.add(exp)
        db.commit()

        from routers.correlations import recompute_experiment_outcomes
        recompute_experiment_outcomes(db=db)
        db.refresh(exp)

        assert exp.weight_delta is None
        assert exp.status == "active"


class TestRecordOutcomeFood:

    def test_counts_matching_entries_during_experiment_week(self, db):
        scratch_week = "2026-W12"
        ws = _week_start(scratch_week)
        for i in range(3):
            db.add(models.FoodEntry(
                raw_input="coffee", name="Coffee", category="food",
                consumed_at=datetime.combine(ws + timedelta(days=i), datetime.min.time()),
            ))
        # A different food, and a matching food outside the experiment week -- neither should count
        db.add(models.FoodEntry(
            raw_input="tea", name="Tea", category="drink",
            consumed_at=datetime.combine(ws + timedelta(days=1), datetime.min.time()),
        ))
        db.add(models.FoodEntry(
            raw_input="coffee", name="Coffee", category="food",
            consumed_at=datetime.combine(ws - timedelta(days=3), datetime.min.time()),
        ))
        exp = models.HealthExperiment(
            week=scratch_week, text="t", food_name="coffee",
            food_target_frequency=0, food_baseline_frequency=4.0,
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, date.today())

        assert exp.food_experiment_count == 3

    def test_zero_when_food_not_logged_that_week(self, db):
        exp = models.HealthExperiment(
            week="2026-W13", text="t", food_name="coffee",
            food_target_frequency=0, food_baseline_frequency=4.0,
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, date.today())

        assert exp.food_experiment_count == 0

    def test_leaves_food_experiment_count_none_when_no_food_name(self, db):
        exp = models.HealthExperiment(week="2026-W10", text="t")
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, date.today())

        assert exp.food_experiment_count is None

    def test_baseline_uses_only_weeks_food_was_actually_present(self, db):
        # 5 consecutive weekly weight readings -> 4 week-over-week delta rows.
        # Food logged in the weeks behind readings[1] and readings[3] only --
        # not the week behind readings[2], and not the experiment week itself.
        base = date(2026, 3, 2)
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            _add_weight(db, (base + timedelta(days=7 * i)).isoformat(), w)
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, base + timedelta(days=35))
        assert len(weight_obs) == 4
        exp_week = weight_obs[-1]["date"]

        for offset in (7, 21):  # weeks behind readings[1] and readings[3]
            d = base + timedelta(days=offset)
            db.add(models.FoodEntry(raw_input="coffee", name="Coffee", category="food",
                                     consumed_at=datetime.combine(d, datetime.min.time())))
            db.add(models.FoodEntry(raw_input="coffee", name="Coffee", category="food",
                                     consumed_at=datetime.combine(d + timedelta(days=1), datetime.min.time())))
        db.commit()

        exp = models.HealthExperiment(
            week=exp_week, text="t", food_name="coffee",
            food_target_frequency=0, food_baseline_frequency=4.0,
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, base + timedelta(days=35))

        assert exp.food_baseline_weeks_n == 2
        expected = (weight_obs[0]["delta_per_day"] + weight_obs[2]["delta_per_day"]) / 2
        assert abs(exp.weight_baseline - expected) < 1e-6
        # The all-other-weeks generic average (weeks 0,1,2) would differ from
        # the food-matched one (weeks 0,2 only) -- confirms the override
        # actually changed the value rather than coincidentally matching.
        generic = sum(r["delta_per_day"] for r in weight_obs[:-1]) / 3
        assert abs(exp.weight_baseline - generic) > 1e-6

    def test_confound_check_compares_average_calories(self, db):
        base = date(2026, 3, 2)
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            _add_weight(db, (base + timedelta(days=7 * i)).isoformat(), w)
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, base + timedelta(days=35))
        exp_week = weight_obs[-1]["date"]

        for offset, cals in ((7, 2000), (21, 2200)):  # the two food-present weeks
            d = base + timedelta(days=offset)
            db.add(models.FoodEntry(raw_input="coffee", name="Coffee", category="food",
                                     consumed_at=datetime.combine(d, datetime.min.time())))
            db.add(models.FoodEntry(raw_input="coffee", name="Coffee", category="food",
                                     consumed_at=datetime.combine(d + timedelta(days=1), datetime.min.time())))
            db.add(models.FoodEntry(raw_input="lunch", name="Lunch", category="food", calories=cals,
                                     consumed_at=datetime.combine(d, datetime.min.time())))
        # Calories logged during the experiment week itself
        exp_day = base + timedelta(days=28)
        db.add(models.FoodEntry(raw_input="lunch", name="Lunch", category="food", calories=1500,
                                 consumed_at=datetime.combine(exp_day, datetime.min.time())))
        db.commit()

        exp = models.HealthExperiment(
            week=exp_week, text="t", food_name="coffee",
            food_target_frequency=0, food_baseline_frequency=4.0,
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, base + timedelta(days=35))

        confounds = json.loads(exp.confounds)
        assert confounds["avg_calories"] == {"baseline": 2100.0, "experiment": 1500.0}

    def test_confound_fields_stay_none_without_a_food_specific_baseline(self, db):
        exp = models.HealthExperiment(
            week="2026-W13", text="t", food_name="coffee",
            food_target_frequency=0, food_baseline_frequency=4.0,
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, date.today())

        assert exp.confounds is None

    def test_falls_back_to_generic_baseline_with_fewer_than_2_present_weeks(self, db):
        base = date(2026, 3, 2)
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            _add_weight(db, (base + timedelta(days=7 * i)).isoformat(), w)
        db.commit()

        weight_obs, _, _ = _load_weekly_obs(db, base + timedelta(days=35))
        exp_week = weight_obs[-1]["date"]

        # Food logged in only ONE other week -- below the 2-week minimum for a
        # food-specific baseline, so the generic all-other-weeks average wins.
        d = base + timedelta(days=7)
        db.add(models.FoodEntry(raw_input="coffee", name="Coffee", category="food",
                                 consumed_at=datetime.combine(d, datetime.min.time())))
        db.add(models.FoodEntry(raw_input="coffee", name="Coffee", category="food",
                                 consumed_at=datetime.combine(d + timedelta(days=1), datetime.min.time())))
        db.commit()

        exp = models.HealthExperiment(
            week=exp_week, text="t", food_name="coffee",
            food_target_frequency=0, food_baseline_frequency=4.0,
        )
        db.add(exp)
        db.commit()

        _record_outcome(exp, db, base + timedelta(days=35))

        assert exp.food_baseline_weeks_n is None
        generic = sum(r["delta_per_day"] for r in weight_obs[:-1]) / 3
        assert abs(exp.weight_baseline - generic) < 1e-6


def _fake_llm_client(payload):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class TestGenerateExperimentRoutines:

    CORR = [{"factor": "x", "outcome": "y", "r": 0.5, "p": 0.01, "n": 10}]

    def test_workout_routine_experiment_persists_fields(self, db):
        for i in range(4):
            _add_workout(db, "row", 1.5, "mi", days_ago=i * 7)
        db.commit()

        payload = {
            "text": "t", "hypothesis": "h", "action": "Row 2 miles every day",
            "health_metric": None, "health_goal": None,
            "routine_type": "workout", "workout_type": "row",
            "workout_target_value": 2.0, "workout_unit": "mi",
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.workout_type == "row"
        assert exp.workout_target_value == 2.0
        assert exp.workout_unit == "mi"
        assert exp.health_metric is None
        assert exp.habit_id is not None
        assert exp.routine_type == "workout"

    def test_food_elimination_experiment_creates_auto_tracked_habit(self, db):
        for i in range(4):
            _add_food_named(db, "protein bar", days_ago=i * 5)  # 4 distinct days within 21
        db.commit()

        payload = {
            "text": "t", "hypothesis": "h", "action": "Limit protein bar to 1x this week",
            "health_metric": None, "health_goal": None,
            "routine_type": "food", "food_name": "protein bar", "food_target_frequency": 1.0,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.food_name == "protein bar"
        assert exp.habit_id is not None
        habit = db.query(models.Habit).filter_by(id=exp.habit_id).first()
        assert habit.food_avoid_name == "protein bar"
        assert habit.food_avoid_target == 1.0
        assert habit.health_metric is None

    def test_unestablished_workout_type_is_discarded(self, db):
        payload = {
            "text": "t", "hypothesis": "h", "action": "Cycle 5 miles",
            "health_metric": None, "health_goal": None,
            "routine_type": "workout", "workout_type": "cycle",
            "workout_target_value": 5.0, "workout_unit": "mi",
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.workout_type is None
        assert exp.workout_target_value is None
        # routine_type is persisted as-given for debugging, independent of
        # whether the workout_type it named was actually established.
        assert exp.routine_type == "workout"

    def test_llm_failure_falls_back_without_crashing(self, db):
        """A malformed/failed LLM call falls back to the canned steps
        experiment -- routine_type must be initialized in that except branch
        too, or building the HealthExperiment row raises a NameError."""
        broken_client = MagicMock()
        broken_client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch("routers.correlations.llm_client", return_value=broken_client):
            exp = _generate_experiment(self.CORR, db)

        assert exp.routine_type is None
        assert exp.action is None

    def test_llm_failure_is_logged_not_silently_swallowed(self, db, capsys):
        """Used to be a bare `except Exception:` with zero logging -- a real,
        deterministically-repeating failure (a reasoning model burning its whole token
        budget on chain-of-thought before ever reaching the JSON answer, truncating it
        every time) produced the same canned fallback experiment on every single
        regeneration with no trace in the logs to explain why."""
        broken_client = MagicMock()
        broken_client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch("routers.correlations.llm_client", return_value=broken_client):
            _generate_experiment(self.CORR, db)

        output = capsys.readouterr().out
        assert "experiment generation error" in output
        assert "boom" in output

    def test_llm_call_requests_json_mode_and_low_reasoning_effort(self, db):
        """Both address the same root cause: a reasoning model (e.g. Groq's gpt-oss) can
        burn the entire max_tokens budget on its internal chain-of-thought -- a separate
        `reasoning` field, not mixed into `content` -- before ever writing the actual JSON
        answer, truncating `content` mid-string every time for a similar prompt. json_object
        mode also guards against stray prose/markdown fences around the JSON regardless."""
        payload = {
            "text": "t", "hypothesis": "h", "action": "Walk 8,000 steps every day",
            "health_metric": "steps", "health_goal": 8000,
            "routine_type": None, "workout_type": None,
            "workout_target_value": None, "workout_unit": None,
        }
        fake_client = _fake_llm_client(payload)
        with patch("routers.correlations.llm_client", return_value=fake_client), \
             patch("deps.LLM_MODEL", "openai/gpt-oss-120b"):
            _generate_experiment(self.CORR, db)

        call_kwargs = fake_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "low"
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert call_kwargs["max_tokens"] >= 600

    def test_habit_routine_clears_health_metric(self, db):
        payload = {
            "text": "t", "hypothesis": "h", "action": "Meditate 15 min",
            "health_metric": "steps", "health_goal": 9000,
            "routine_type": "habit", "workout_type": None,
            "workout_target_value": None, "workout_unit": None,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.health_metric is None
        assert exp.health_goal is None
        assert exp.workout_type is None
        assert exp.action == "Meditate 15 min"

    def test_backstop_nudges_repeated_health_goal(self, db):
        db.add(models.HealthExperiment(
            week="2020-W01", text="t", health_metric="steps", health_goal=8000.0, status="dismissed",
        ))
        db.commit()

        payload = {
            "text": "t", "hypothesis": "h", "action": "Walk 8000 steps",
            "health_metric": "steps", "health_goal": 8000,
            "routine_type": None, "workout_type": None,
            "workout_target_value": None, "workout_unit": None,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.health_goal == 9600.0

    def test_prompt_includes_established_routines_and_recent_experiments(self, db):
        for i in range(4):
            _add_workout(db, "row", 1.5, "mi", days_ago=i * 7)
        db.add(models.HealthExperiment(
            week="2020-W01", text="t", health_metric="steps", health_goal=8000.0, status="dismissed",
        ))
        db.commit()

        payload = {
            "text": "t", "hypothesis": "h", "action": None,
            "health_metric": None, "health_goal": None,
            "routine_type": None, "workout_type": None,
            "workout_target_value": None, "workout_unit": None,
        }
        fake_client = _fake_llm_client(payload)
        with patch("routers.correlations.llm_client", return_value=fake_client):
            _generate_experiment(self.CORR, db)

        sent_content = fake_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "Established routines" in sent_content
        assert "row" in sent_content
        assert "Recently tried" in sent_content
        assert "steps: 8000" in sent_content

    def test_food_routine_experiment_persists_fields(self, db):
        for i in range(4):
            _add_food_named(db, "coffee", days_ago=i * 5)
        db.commit()

        payload = {
            "text": "t", "hypothesis": "h", "action": "Cut out coffee entirely this week",
            "health_metric": None, "health_goal": None,
            "routine_type": "food", "food_name": "coffee", "food_target_frequency": 0,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.food_name == "coffee"
        assert exp.food_target_frequency == 0
        assert exp.food_baseline_frequency is not None
        assert exp.health_metric is None
        assert exp.workout_type is None
        assert exp.habit_id is not None

    def test_unestablished_food_name_is_discarded(self, db):
        payload = {
            "text": "t", "hypothesis": "h", "action": "Cut out donuts entirely this week",
            "health_metric": None, "health_goal": None,
            "routine_type": "food", "food_name": "donuts", "food_target_frequency": 0,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.food_name is None
        assert exp.food_target_frequency is None

    def test_repeated_food_name_is_dropped(self, db):
        for i in range(4):
            _add_food_named(db, "coffee", days_ago=i * 5)
        db.add(models.HealthExperiment(
            week="2020-W01", text="t", food_name="coffee",
            food_target_frequency=0, food_baseline_frequency=4.0, status="dismissed",
        ))
        db.commit()

        payload = {
            "text": "t", "hypothesis": "h", "action": "Cut out coffee entirely this week",
            "health_metric": None, "health_goal": None,
            "routine_type": "food", "food_name": "coffee", "food_target_frequency": 0,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.food_name is None
        assert exp.food_target_frequency is None

    def test_prompt_includes_frequently_eaten_foods(self, db):
        for i in range(4):
            _add_food_named(db, "coffee", days_ago=i * 5)
        db.commit()

        payload = {
            "text": "t", "hypothesis": "h", "action": None,
            "health_metric": None, "health_goal": None,
            "routine_type": None, "food_name": None, "food_target_frequency": None,
        }
        fake_client = _fake_llm_client(payload)
        with patch("routers.correlations.llm_client", return_value=fake_client):
            _generate_experiment(self.CORR, db)

        sent_content = fake_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "Frequently eaten foods" in sent_content
        assert "coffee" in sent_content


class TestRecentAvgSteps:

    def test_averages_steps_over_the_trailing_window(self, db):
        today = date(2026, 6, 20)
        for i, v in enumerate([5000.0, 6000.0, 7000.0]):
            db.add(models.WithingsMeasurement(
                date=(today - timedelta(days=i)).isoformat(), metric="steps", value=v,
            ))
        db.commit()

        assert _recent_avg_steps(db, today, days=28) == 6000.0

    def test_ignores_measurements_outside_the_window(self, db):
        today = date(2026, 6, 20)
        db.add(models.WithingsMeasurement(date=(today - timedelta(days=5)).isoformat(), metric="steps", value=8000.0))
        db.add(models.WithingsMeasurement(date=(today - timedelta(days=40)).isoformat(), metric="steps", value=1000.0))
        db.commit()

        assert _recent_avg_steps(db, today, days=28) == 8000.0

    def test_ignores_other_metrics(self, db):
        today = date(2026, 6, 20)
        db.add(models.WithingsMeasurement(date=today.isoformat(), metric="weight", value=80.0))
        db.commit()

        assert _recent_avg_steps(db, today, days=28) is None

    def test_returns_none_with_no_step_data(self, db):
        assert _recent_avg_steps(db, date(2026, 6, 20), days=28) is None


class TestGenerateExperimentStepGoalFallback:
    """The LLM is instructed to always give a concrete step number, but sometimes proposes
    a relative percentage instead (e.g. "increase your daily step count by 10% compared to
    your recent average") with no number anywhere in action or hypothesis -- previously this
    meant health_metric/health_goal both ended up null (no auto-tracked goal at all) even
    though the intent -- and the data needed to compute a real number -- was right there."""

    CORR = [{"factor": "x", "outcome": "y", "r": 0.5, "p": 0.01, "n": 10}]

    def test_percentage_only_action_computes_a_real_goal_from_recent_average(self, db):
        for i in range(14):
            db.add(models.WithingsMeasurement(
                date=(date.today() - timedelta(days=i)).isoformat(), metric="steps", value=6000.0,
            ))
        db.commit()

        payload = {
            "text": "t",
            "hypothesis": "More consistent movement should correlate with better weight outcomes.",
            "action": "Try increasing your daily step count by 10% compared to your recent average.",
            "health_metric": "steps", "health_goal": None,
            "routine_type": None, "workout_type": None,
            "workout_target_value": None, "workout_unit": None,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.health_metric == "steps"
        assert exp.health_goal == 6600.0  # 6000 * 1.10

    def test_singular_step_phrasing_is_still_recognized(self, db):
        """Both the "steps" mention-detection and the number-extraction regex used to be
        hardcoded to the plural -- "9,000 step target" (singular, number directly adjacent)
        previously matched neither."""
        payload = {
            "text": "t", "hypothesis": "h",
            "action": "Hit a 9,000 step target every day.",
            "health_metric": None, "health_goal": None,
            "routine_type": None, "workout_type": None,
            "workout_target_value": None, "workout_unit": None,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.health_metric == "steps"
        assert exp.health_goal == 9000.0

    def test_percentage_with_no_recent_step_data_leaves_goal_unset(self, db):
        """No Withings step data at all to compute a relative target from -- must not crash,
        and must not invent a number out of nothing."""
        payload = {
            "text": "t", "hypothesis": "No steps data on hand.",
            "action": "Try increasing your daily step count by 10% compared to your recent average.",
            "health_metric": "steps", "health_goal": None,
            "routine_type": None, "workout_type": None,
            "workout_target_value": None, "workout_unit": None,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.health_metric is None
        assert exp.health_goal is None

    def test_absolute_number_still_takes_priority_over_a_percentage_in_the_same_text(self, db):
        for i in range(7):
            db.add(models.WithingsMeasurement(
                date=(date.today() - timedelta(days=i)).isoformat(), metric="steps", value=6000.0,
            ))
        db.commit()

        payload = {
            "text": "t", "hypothesis": "h",
            "action": "Walk 8,000 steps every day (about 10% more than usual).",
            "health_metric": "steps", "health_goal": None,
            "routine_type": None, "workout_type": None,
            "workout_target_value": None, "workout_unit": None,
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.health_goal == 8000.0


class TestCheckHabitForWorkout:

    def _active_experiment(self, db, workout_type="row", habit_id=None, week=None):
        # Matches the fixed date(2026, 6, 20) every test in this class passes to
        # check_habit_for_workout -- previously this defaulted to _current_isoweek()
        # with no argument (real wall-clock "today"), which only ever passed because
        # check_habit_for_workout itself had the same bug (computed its week lookup
        # from real "today" too, ignoring the date it was actually given). Fixing that
        # bug means this fixture must now agree with the fixed test date explicitly.
        exp = models.HealthExperiment(
            week=week or _current_isoweek(date(2026, 6, 20)), text="t", status="active",
            workout_type=workout_type, workout_target_value=2.0, workout_unit="mi",
            habit_id=habit_id,
        )
        db.add(exp)
        db.commit()
        return exp

    def _habit(self, db, name="🧪 Row 2 miles", archived=False):
        habit = models.Habit(name=name, archived=archived)
        db.add(habit)
        db.commit()
        return habit

    def test_matching_active_experiment_checks_the_linked_habit(self, db):
        habit = self._habit(db)
        self._active_experiment(db, workout_type="row", habit_id=habit.id)

        result = check_habit_for_workout(db, "row", date(2026, 6, 20))

        assert result is not None
        assert result.id == habit.id
        completion = db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date="2026-06-20",
        ).first()
        assert completion is not None

    def test_no_active_experiment_is_a_noop(self, db):
        habit = self._habit(db)
        self._active_experiment(db, workout_type="row", habit_id=habit.id)
        db.query(models.HealthExperiment).update({"status": "dismissed"})
        db.commit()

        result = check_habit_for_workout(db, "row", date(2026, 6, 20))

        assert result is None
        assert db.query(models.HabitCompletion).count() == 0

    def test_workout_type_mismatch_is_a_noop(self, db):
        habit = self._habit(db)
        self._active_experiment(db, workout_type="row", habit_id=habit.id)

        result = check_habit_for_workout(db, "run", date(2026, 6, 20))

        assert result is None
        assert db.query(models.HabitCompletion).count() == 0

    def test_already_checked_today_is_idempotent(self, db):
        habit = self._habit(db)
        self._active_experiment(db, workout_type="row", habit_id=habit.id)
        db.add(models.HabitCompletion(habit_id=habit.id, date="2026-06-20"))
        db.commit()

        result = check_habit_for_workout(db, "row", date(2026, 6, 20))

        assert result is not None
        assert db.query(models.HabitCompletion).filter_by(habit_id=habit.id).count() == 1

    def test_no_linked_habit_is_a_noop(self, db):
        self._active_experiment(db, workout_type="row", habit_id=None)

        result = check_habit_for_workout(db, "row", date(2026, 6, 20))

        assert result is None
        assert db.query(models.HabitCompletion).count() == 0

    def test_archived_habit_is_a_noop(self, db):
        habit = self._habit(db, archived=True)
        self._active_experiment(db, workout_type="row", habit_id=habit.id)

        result = check_habit_for_workout(db, "row", date(2026, 6, 20))

        assert result is None
        assert db.query(models.HabitCompletion).count() == 0

    def test_missing_habit_row_is_a_noop(self, db):
        self._active_experiment(db, workout_type="row", habit_id=9999)

        result = check_habit_for_workout(db, "row", date(2026, 6, 20))

        assert result is None
        assert db.query(models.HabitCompletion).count() == 0

    def test_experiment_from_a_different_week_does_not_match(self, db):
        habit = self._habit(db)
        self._active_experiment(db, workout_type="row", habit_id=habit.id, week="2020-W01")

        result = check_habit_for_workout(db, "row", date(2026, 6, 20))

        assert result is None
        assert db.query(models.HabitCompletion).count() == 0


class TestCheckWorkoutForHabit:
    """The mirror direction of TestCheckHabitForWorkout: checking off the habit should
    auto-log a workout at the experiment's target value (the "row 1.2 miles" bug report
    this was built for -- checking the habit never touched workout logs at all)."""

    def _active_experiment(self, db, workout_type="row", habit_id=None, week=None,
                            target_value=1.2, unit="mile", action="Row 1.2 miles"):
        exp = models.HealthExperiment(
            week=week or _current_isoweek(date(2026, 6, 20)), text="t", status="active",
            action=action, workout_type=workout_type, workout_target_value=target_value,
            workout_unit=unit, habit_id=habit_id,
        )
        db.add(exp)
        db.commit()
        return exp

    def _habit(self, db, name="🧪 Row 1.2 miles", archived=False):
        habit = models.Habit(name=name, archived=archived)
        db.add(habit)
        db.commit()
        return habit

    def test_logs_a_workout_at_the_target_value(self, db):
        habit = self._habit(db)
        self._active_experiment(db, habit_id=habit.id)

        entry = check_workout_for_habit(db, habit.id, date(2026, 6, 20))

        assert entry is not None
        assert entry.type == "row"
        assert entry.value == 1.2
        assert entry.unit == "mile"
        assert entry.logged_at.date() == date(2026, 6, 20)

    def test_no_active_experiment_is_a_noop(self, db):
        habit = self._habit(db)
        result = check_workout_for_habit(db, habit.id, date(2026, 6, 20))
        assert result is None
        assert db.query(models.WorkoutEntry).count() == 0

    def test_habit_not_linked_to_this_experiment_is_a_noop(self, db):
        habit = self._habit(db)
        other_habit = self._habit(db, name="other")
        self._active_experiment(db, habit_id=other_habit.id)

        result = check_workout_for_habit(db, habit.id, date(2026, 6, 20))
        assert result is None
        assert db.query(models.WorkoutEntry).count() == 0

    def test_experiment_without_a_workout_type_is_a_noop(self, db):
        """A plain habit/Withings-metric experiment shouldn't ever auto-log a
        workout, even if it happens to have a habit_id."""
        habit = self._habit(db)
        db.add(models.HealthExperiment(
            week=_current_isoweek(date(2026, 6, 20)), text="t", status="active",
            habit_id=habit.id,
        ))
        db.commit()

        result = check_workout_for_habit(db, habit.id, date(2026, 6, 20))
        assert result is None
        assert db.query(models.WorkoutEntry).count() == 0

    def test_experiment_from_a_different_week_does_not_match(self, db):
        habit = self._habit(db)
        self._active_experiment(db, habit_id=habit.id, week="2020-W01")

        result = check_workout_for_habit(db, habit.id, date(2026, 6, 20))
        assert result is None
        assert db.query(models.WorkoutEntry).count() == 0

    def test_dismissed_experiment_is_a_noop(self, db):
        habit = self._habit(db)
        exp = self._active_experiment(db, habit_id=habit.id)
        exp.status = "dismissed"
        db.commit()

        result = check_workout_for_habit(db, habit.id, date(2026, 6, 20))
        assert result is None
        assert db.query(models.WorkoutEntry).count() == 0

    def test_a_real_workout_already_logged_today_takes_precedence(self, db):
        habit = self._habit(db)
        self._active_experiment(db, habit_id=habit.id)
        db.add(models.WorkoutEntry(
            type="row", value=2.5, unit="mi", raw_input="rowed 2.5 mi",
            logged_at=datetime(2026, 6, 20, 9, 0),
        ))
        db.commit()

        result = check_workout_for_habit(db, habit.id, date(2026, 6, 20))

        assert result is None
        entries = db.query(models.WorkoutEntry).all()
        assert len(entries) == 1
        assert entries[0].value == 2.5  # the real entry, untouched

    def test_does_not_double_log_if_called_twice(self, db):
        habit = self._habit(db)
        self._active_experiment(db, habit_id=habit.id)

        first = check_workout_for_habit(db, habit.id, date(2026, 6, 20))
        second = check_workout_for_habit(db, habit.id, date(2026, 6, 20))

        assert first is not None
        assert second is None
        assert db.query(models.WorkoutEntry).count() == 1

    def test_a_matching_workout_from_a_different_day_does_not_block_todays_autolog(self, db):
        habit = self._habit(db)
        self._active_experiment(db, habit_id=habit.id)
        db.add(models.WorkoutEntry(
            type="row", value=1.2, unit="mi", raw_input="rowed yesterday",
            logged_at=datetime(2026, 6, 19, 9, 0),
        ))
        db.commit()

        result = check_workout_for_habit(db, habit.id, date(2026, 6, 20))
        assert result is not None
        assert db.query(models.WorkoutEntry).count() == 2


class TestCheckFoodAvoidanceHabits:
    """check_food_avoidance_habits is the mirror-image of check_habit_for_workout: it
    auto-checks a food-elimination habit for trailing days the target food was NOT logged,
    since absence can't be reacted to as an event the way a WorkoutEntry can be."""

    def _habit(self, db, food_avoid_name="protein bar", food_avoid_target=1.0, archived=False):
        habit = models.Habit(
            name="🧪 Limit protein bar", archived=archived,
            food_avoid_name=food_avoid_name, food_avoid_target=food_avoid_target,
        )
        db.add(habit)
        db.commit()
        return habit

    def _experiment(self, db, habit_id, food_name="protein bar", week=None):
        exp = models.HealthExperiment(
            week=week or _current_isoweek(date(2026, 6, 20)), text="t", status="active",
            food_name=food_name, food_target_frequency=1.0, habit_id=habit_id,
        )
        db.add(exp)
        db.commit()
        return exp

    def _food_entry(self, db, name, on_date: date):
        db.add(models.FoodEntry(
            raw_input=name, name=name, category="food",
            consumed_at=datetime.combine(on_date, datetime.min.time()).replace(hour=12),
        ))
        db.commit()

    def test_checks_off_trailing_days_the_food_was_not_logged(self, db):
        habit = self._habit(db)
        week = _current_isoweek(date(2026, 6, 20))
        ws = _week_start(week)
        self._experiment(db, habit.id, week=week)
        today = ws + timedelta(days=5)  # comfortably within the week, 3 trailing days available

        check_food_avoidance_habits(db, today)

        for days_back in range(1, 4):
            day_str = (today - timedelta(days=days_back)).isoformat()
            assert db.query(models.HabitCompletion).filter_by(
                habit_id=habit.id, date=day_str,
            ).first() is not None

    def test_does_not_check_today(self, db):
        habit = self._habit(db)
        week = _current_isoweek(date(2026, 6, 20))
        ws = _week_start(week)
        self._experiment(db, habit.id, week=week)
        today = ws + timedelta(days=5)

        check_food_avoidance_habits(db, today)

        assert db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date=today.isoformat(),
        ).first() is None

    def test_a_day_the_food_was_logged_is_not_checked(self, db):
        habit = self._habit(db)
        week = _current_isoweek(date(2026, 6, 20))
        ws = _week_start(week)
        self._experiment(db, habit.id, week=week)
        today = ws + timedelta(days=5)
        ate_on = today - timedelta(days=1)
        self._food_entry(db, "protein bar", ate_on)

        check_food_avoidance_habits(db, today)

        assert db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date=ate_on.isoformat(),
        ).first() is None
        # the other trailing days, with nothing logged, still get checked
        assert db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date=(today - timedelta(days=2)).isoformat(),
        ).first() is not None

    def test_food_match_is_case_insensitive(self, db):
        habit = self._habit(db, food_avoid_name="protein bar")
        week = _current_isoweek(date(2026, 6, 20))
        ws = _week_start(week)
        self._experiment(db, habit.id, week=week)
        today = ws + timedelta(days=5)
        ate_on = today - timedelta(days=1)
        self._food_entry(db, "Protein Bar", ate_on)

        check_food_avoidance_habits(db, today)

        assert db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date=ate_on.isoformat(),
        ).first() is None

    def test_lookback_does_not_cross_into_the_prior_week(self, db):
        habit = self._habit(db)
        week = _current_isoweek(date(2026, 6, 20))
        ws = _week_start(week)
        self._experiment(db, habit.id, week=week)
        today = ws + timedelta(days=1)  # only 1 day into the experiment's week

        check_food_avoidance_habits(db, today)

        # today - 1 == the week's Monday: in-bounds, gets checked
        assert db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date=ws.isoformat(),
        ).first() is not None
        # today - 2 / today - 3 fall in the prior week: must not be touched
        assert db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date=(ws - timedelta(days=1)).isoformat(),
        ).first() is None
        assert db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date=(ws - timedelta(days=2)).isoformat(),
        ).first() is None

    def test_archived_habit_is_left_alone(self, db):
        habit = self._habit(db, archived=True)
        week = _current_isoweek(date(2026, 6, 20))
        ws = _week_start(week)
        self._experiment(db, habit.id, week=week)
        today = ws + timedelta(days=5)

        check_food_avoidance_habits(db, today)

        assert db.query(models.HabitCompletion).count() == 0

    def test_already_checked_day_is_left_alone(self, db):
        habit = self._habit(db)
        week = _current_isoweek(date(2026, 6, 20))
        ws = _week_start(week)
        self._experiment(db, habit.id, week=week)
        today = ws + timedelta(days=5)
        pre_checked = (today - timedelta(days=1)).isoformat()
        db.add(models.HabitCompletion(habit_id=habit.id, date=pre_checked))
        db.commit()

        check_food_avoidance_habits(db, today)

        assert db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date=pre_checked,
        ).count() == 1

    def test_regular_habit_without_food_avoid_name_is_untouched(self, db):
        habit = models.Habit(name="Read 30 minutes")
        db.add(habit)
        db.commit()

        check_food_avoidance_habits(db, date(2026, 6, 25))

        assert db.query(models.HabitCompletion).count() == 0


class TestCheckHabitRowWorkoutAutoLogWiring:
    """check_habit_row is the single place this gets wired in (see its from_workout
    docstring) -- these confirm the manual path triggers it and the workout-triggered
    path doesn't loop back and double-log itself."""

    def _linked_habit_and_experiment(self, db):
        habit = models.Habit(name="🧪 Row 1.2 miles")
        db.add(habit)
        db.commit()
        db.add(models.HealthExperiment(
            week=_current_isoweek(date(2026, 6, 20)), text="t", status="active",
            action="Row 1.2 miles", workout_type="row", workout_target_value=1.2,
            workout_unit="mile", habit_id=habit.id,
        ))
        db.commit()
        return habit

    def test_manual_checkoff_auto_logs_the_linked_workout(self, db):
        habit = self._linked_habit_and_experiment(db)

        check_habit_row(db, habit.id, date(2026, 6, 20))

        entries = db.query(models.WorkoutEntry).all()
        assert len(entries) == 1
        assert entries[0].type == "row"
        assert entries[0].value == 1.2

    def test_workout_triggered_checkoff_does_not_double_log(self, db):
        habit = self._linked_habit_and_experiment(db)

        check_habit_for_workout(db, "row", date(2026, 6, 20))

        assert db.query(models.WorkoutEntry).count() == 0

    def test_already_completed_today_does_not_re_trigger_the_autolog(self, db):
        """Re-checking an already-completed habit today is a no-op for the completion
        itself, and must not attempt a second auto-log alongside it."""
        habit = self._linked_habit_and_experiment(db)
        check_habit_row(db, habit.id, date(2026, 6, 20))
        assert db.query(models.WorkoutEntry).count() == 1

        check_habit_row(db, habit.id, date(2026, 6, 20))
        assert db.query(models.WorkoutEntry).count() == 1
