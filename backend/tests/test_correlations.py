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
    _week_start,
)


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
        # Deliberately still the OLD key names -- this simulates a JSON blob
        # written before the health_metric/health_goal rename, which
        # _migrate_appsetting must keep reading correctly regardless of what
        # the live model calls the field now.
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

        weight_obs, _ = _load_weekly_obs(db, base + timedelta(days=35))
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

    def test_falls_back_to_generic_baseline_with_fewer_than_2_present_weeks(self, db):
        base = date(2026, 3, 2)
        for i, w in enumerate([80.0, 80.7, 80.7, 81.4, 81.4]):
            _add_weight(db, (base + timedelta(days=7 * i)).isoformat(), w)
        db.commit()

        weight_obs, _ = _load_weekly_obs(db, base + timedelta(days=35))
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
            "needs_habit": True, "health_metric": None, "health_goal": None,
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

    def test_unestablished_workout_type_is_discarded(self, db):
        payload = {
            "text": "t", "hypothesis": "h", "action": "Cycle 5 miles",
            "needs_habit": True, "health_metric": None, "health_goal": None,
            "routine_type": "workout", "workout_type": "cycle",
            "workout_target_value": 5.0, "workout_unit": "mi",
        }
        with patch("routers.correlations.llm_client", return_value=_fake_llm_client(payload)):
            exp = _generate_experiment(self.CORR, db)

        assert exp.workout_type is None
        assert exp.workout_target_value is None

    def test_habit_routine_clears_health_metric(self, db):
        payload = {
            "text": "t", "hypothesis": "h", "action": "Meditate 15 min",
            "needs_habit": True, "health_metric": "steps", "health_goal": 9000,
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
            "needs_habit": True, "health_metric": "steps", "health_goal": 8000,
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
            "text": "t", "hypothesis": "h", "action": None, "needs_habit": False,
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
            "needs_habit": True, "health_metric": None, "health_goal": None,
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
            "needs_habit": True, "health_metric": None, "health_goal": None,
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
            "needs_habit": True, "health_metric": None, "health_goal": None,
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
            "text": "t", "hypothesis": "h", "action": None, "needs_habit": False,
            "health_metric": None, "health_goal": None,
            "routine_type": None, "food_name": None, "food_target_frequency": None,
        }
        fake_client = _fake_llm_client(payload)
        with patch("routers.correlations.llm_client", return_value=fake_client):
            _generate_experiment(self.CORR, db)

        sent_content = fake_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "Frequently eaten foods" in sent_content
        assert "coffee" in sent_content
