"""
Unit tests for Withings health integration.

Two test suites — no LLM, no network, no Withings credentials required:

1. _auto_check_habits — verifies goal-met logic against an in-memory SQLite DB
2. post_process health metric detection — verifies regex extraction from habit text
"""

import sys
import os
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ── Path setup ────────────────────────────────────────────────────────────────
# Allow imports from the backend root (models, database, etc.)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Base
import models
from routers.withings import _auto_check_habits
from model_plugins.base import BaseModelPlugin
from schemas import ParsedCard

# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _add_habit(db, name: str, metric: str, goal: float) -> models.Habit:
    h = models.Habit(name=name, health_metric=metric, health_goal=goal)
    db.add(h)
    db.flush()
    return h


def _add_measurement(db, date_str: str, metric: str, value: float) -> None:
    db.add(models.WithingsMeasurement(
        date=date_str, metric=metric, value=value,
        synced_at=datetime.now(timezone.utc),
    ))
    db.flush()


def _completed_today(db, habit_id: int, date_str: str) -> bool:
    return db.query(models.HabitCompletion).filter_by(
        habit_id=habit_id, date=date_str
    ).first() is not None


# ── _auto_check_habits ────────────────────────────────────────────────────────

TODAY = date(2026, 6, 20)
TODAY_STR = TODAY.isoformat()


class TestAutoCheckHabits:

    def test_steps_goal_met(self, db):
        h = _add_habit(db, "Walk 10k steps", "steps", 10_000)
        _add_measurement(db, TODAY_STR, "steps", 10_500)
        _auto_check_habits(db, TODAY)
        assert _completed_today(db, h.id, TODAY_STR)

    def test_steps_goal_exactly_met(self, db):
        h = _add_habit(db, "Walk 10k steps", "steps", 10_000)
        _add_measurement(db, TODAY_STR, "steps", 10_000)
        _auto_check_habits(db, TODAY)
        assert _completed_today(db, h.id, TODAY_STR)

    def test_steps_goal_not_met(self, db):
        h = _add_habit(db, "Walk 10k steps", "steps", 10_000)
        _add_measurement(db, TODAY_STR, "steps", 9_999)
        _auto_check_habits(db, TODAY)
        assert not _completed_today(db, h.id, TODAY_STR)

    def test_fat_ratio_goal_met(self, db):
        h = _add_habit(db, "Keep fat low", "fat_ratio", 20.0)
        _add_measurement(db, TODAY_STR, "fat_ratio", 19.5)
        _auto_check_habits(db, TODAY)
        assert _completed_today(db, h.id, TODAY_STR)

    def test_fat_ratio_goal_exactly_met(self, db):
        h = _add_habit(db, "Keep fat low", "fat_ratio", 20.0)
        _add_measurement(db, TODAY_STR, "fat_ratio", 20.0)
        _auto_check_habits(db, TODAY)
        assert _completed_today(db, h.id, TODAY_STR)

    def test_fat_ratio_goal_not_met(self, db):
        h = _add_habit(db, "Keep fat low", "fat_ratio", 20.0)
        _add_measurement(db, TODAY_STR, "fat_ratio", 20.1)
        _auto_check_habits(db, TODAY)
        assert not _completed_today(db, h.id, TODAY_STR)

    def test_weight_goal_met(self, db):
        h = _add_habit(db, "Stay under 75 kg", "weight", 75.0)
        _add_measurement(db, TODAY_STR, "weight", 74.5)
        _auto_check_habits(db, TODAY)
        assert _completed_today(db, h.id, TODAY_STR)

    def test_weight_goal_not_met(self, db):
        h = _add_habit(db, "Stay under 75 kg", "weight", 75.0)
        _add_measurement(db, TODAY_STR, "weight", 75.1)
        _auto_check_habits(db, TODAY)
        assert not _completed_today(db, h.id, TODAY_STR)

    def test_no_measurement_no_completion(self, db):
        h = _add_habit(db, "Walk 10k steps", "steps", 10_000)
        # no measurement added
        _auto_check_habits(db, TODAY)
        assert not _completed_today(db, h.id, TODAY_STR)

    def test_no_goal_no_completion(self, db):
        # Habit has metric but no goal — should not be auto-checked
        h = models.Habit(name="Walk", health_metric="steps", health_goal=None)
        db.add(h)
        db.flush()
        _add_measurement(db, TODAY_STR, "steps", 12_000)
        _auto_check_habits(db, TODAY)
        assert not _completed_today(db, h.id, TODAY_STR)

    def test_archived_habit_not_completed(self, db):
        h = models.Habit(name="Walk", health_metric="steps", health_goal=10_000, archived=True)
        db.add(h)
        db.flush()
        _add_measurement(db, TODAY_STR, "steps", 12_000)
        _auto_check_habits(db, TODAY)
        assert not _completed_today(db, h.id, TODAY_STR)

    def test_idempotent_does_not_duplicate(self, db):
        h = _add_habit(db, "Walk 10k steps", "steps", 10_000)
        _add_measurement(db, TODAY_STR, "steps", 12_000)
        _auto_check_habits(db, TODAY)
        _auto_check_habits(db, TODAY)  # run again
        count = db.query(models.HabitCompletion).filter_by(
            habit_id=h.id, date=TODAY_STR
        ).count()
        assert count == 1

    def test_multiple_habits_same_metric(self, db):
        h_high = _add_habit(db, "Walk 15k steps", "steps", 15_000)
        h_low  = _add_habit(db, "Walk 5k steps",  "steps", 5_000)
        _add_measurement(db, TODAY_STR, "steps", 12_000)
        _auto_check_habits(db, TODAY)
        assert not _completed_today(db, h_high.id, TODAY_STR)
        assert _completed_today(db, h_low.id, TODAY_STR)

    def test_recent_past_date_within_lookback_is_retroactively_completed(self, db):
        """Regression test: a device (e.g. a Withings watch) can finalize a
        day's step count late -- sometimes after local midnight -- so the
        measurement for that day only lands in our DB once "today" has
        already rolled over. The habit for that day must still get checked
        off once the goal-meeting measurement arrives, not just for the
        current day."""
        h = _add_habit(db, "Walk 10k steps", "steps", 10_000)
        _add_measurement(db, "2026-06-19", "steps", 12_000)  # yesterday, goal met
        _auto_check_habits(db, TODAY)
        assert _completed_today(db, h.id, "2026-06-19")
        # Today itself has no measurement, so it should not be completed
        assert not _completed_today(db, h.id, TODAY_STR)

    def test_date_outside_lookback_window_not_completed(self, db):
        h = _add_habit(db, "Walk 10k steps", "steps", 10_000)
        _add_measurement(db, "2026-06-10", "steps", 12_000)  # 10 days ago, outside the window
        _auto_check_habits(db, TODAY)
        assert not _completed_today(db, h.id, "2026-06-10")

    def test_lookback_does_not_duplicate_completions_across_syncs(self, db):
        h = _add_habit(db, "Walk 10k steps", "steps", 10_000)
        _add_measurement(db, "2026-06-19", "steps", 12_000)
        _auto_check_habits(db, TODAY)
        _auto_check_habits(db, TODAY)  # a later sync re-checks the same window
        count = db.query(models.HabitCompletion).filter_by(
            habit_id=h.id, date="2026-06-19"
        ).count()
        assert count == 1


# ── Health metric detection in post_process ───────────────────────────────────

plugin = BaseModelPlugin()


def _habit(text: str, **kwargs) -> ParsedCard:
    """Run post_process on a habit ParsedCard with the given input text."""
    defaults = dict(type="habit", title=text, section="today", recurrence_rule="daily")
    defaults.update(kwargs)
    parsed = ParsedCard(**defaults)
    return plugin.post_process(parsed, text=text)


class TestHealthMetricDetection:

    # ── Steps ─────────────────────────────────────────────────────────────────

    def test_steps_plain_number(self):
        r = _habit("walk 10000 steps a day")
        assert r.health_metric == "steps"
        assert r.health_goal == 10_000

    def test_steps_comma_formatted(self):
        r = _habit("10,000 steps per day")
        assert r.health_metric == "steps"
        assert r.health_goal == 10_000

    def test_steps_k_shorthand(self):
        r = _habit("walk 10k steps daily")
        assert r.health_metric == "steps"
        assert r.health_goal == 10_000

    def test_steps_5k_shorthand(self):
        r = _habit("add a habit for 5k steps a day")
        assert r.health_metric == "steps"
        assert r.health_goal == 5_000

    def test_steps_add_habit_phrase(self):
        r = _habit("add a habit with 5,000 steps per day")
        assert r.health_metric == "steps"
        assert r.health_goal == 5_000

    def test_steps_singular(self):
        r = _habit("take 8000 step a day")
        assert r.health_metric == "steps"
        assert r.health_goal == 8_000

    # ── Body fat ──────────────────────────────────────────────────────────────

    def test_fat_ratio_basic(self):
        r = _habit("keep body fat under 20%")
        assert r.health_metric == "fat_ratio"
        assert r.health_goal == 20.0

    def test_fat_ratio_reversed(self):
        r = _habit("stay at 18.5% body fat")
        assert r.health_metric == "fat_ratio"
        assert r.health_goal == 18.5

    def test_fat_ratio_goal_phrase(self):
        r = _habit("body fat 20%")
        assert r.health_metric == "fat_ratio"
        assert r.health_goal == 20.0

    # ── Weight ────────────────────────────────────────────────────────────────

    def test_weight_kg(self):
        r = _habit("weigh less than 75 kg")
        assert r.health_metric == "weight"
        assert r.health_goal == 75.0

    def test_weight_lbs(self):
        r = _habit("stay under 165 lbs")
        assert r.health_metric == "weight"
        assert abs(r.health_goal - 74.8) < 0.2  # 165 * 0.453592 ≈ 74.8

    def test_weight_pounds(self):
        r = _habit("weigh under 180 pounds")
        assert r.health_metric == "weight"
        assert abs(r.health_goal - 81.6) < 0.2  # 180 * 0.453592 ≈ 81.6

    # ── Non-health habits — must NOT get a metric ─────────────────────────────

    def test_no_metric_for_meditation(self):
        r = _habit("meditate every morning")
        assert r.health_metric is None
        assert r.health_goal is None

    def test_no_metric_for_journal(self):
        r = _habit("journal every night")
        assert r.health_metric is None

    def test_no_metric_for_task(self):
        # type=task should never get a metric even with steps in title
        parsed = ParsedCard(type="task", title="buy 10000 steps tracker", section="later")
        result = plugin.post_process(parsed, text="buy 10000 steps tracker")
        assert result.health_metric is None

    # ── Goal-type detection ───────────────────────────────────────────────────

    def test_goal_set_weight(self):
        parsed = ParsedCard(type="task", title="set weight goal", section="later")
        result = plugin.post_process(parsed, text="set my weight goal to 75 kg")
        assert result.type == "goal"
        assert result.health_metric == "weight"
        assert result.health_goal == 75.0

    def test_goal_change_steps(self):
        parsed = ParsedCard(type="task", title="change step goal", section="later")
        result = plugin.post_process(parsed, text="change my step goal to 10,000")
        assert result.type == "goal"
        assert result.health_metric == "steps"
        assert result.health_goal == 10_000

    def test_goal_update_fat(self):
        parsed = ParsedCard(type="task", title="update fat goal", section="later")
        result = plugin.post_process(parsed, text="update my body fat goal to 18%")
        assert result.type == "goal"
        assert result.health_metric == "fat_ratio"
        assert result.health_goal == 18.0

    def test_regular_habit_not_goal(self):
        """"Walk 10k steps daily" is a habit, not a goal-setter."""
        r = _habit("walk 10k steps daily")
        assert r.type == "habit"
        assert r.health_metric == "steps"

    # ── LLM-set values are preserved (not overridden by regex) ───────────────

    def test_llm_metric_not_overridden(self):
        parsed = ParsedCard(
            type="habit", title="walk daily", section="today",
            recurrence_rule="daily",
            health_metric="steps", health_goal=8_000,
        )
        result = plugin.post_process(parsed, text="walk daily")
        assert result.health_metric == "steps"
        assert result.health_goal == 8_000
