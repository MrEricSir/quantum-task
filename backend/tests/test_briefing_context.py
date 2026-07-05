"""
Unit tests for briefing_context.py — fmt_time, event_local_date,
build_today_context, build_week_context, and compute_observations.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from briefing_context import (
    fmt_time,
    event_local_date,
    build_today_context,
    build_week_context,
    compute_observations,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_event(title="Meeting", hour=14, minute=0, all_day=False, day_offset=0):
    """Create a mock calendar event."""
    base = datetime(2026, 6, 3, hour, minute)
    if day_offset:
        base = datetime(2026, 6, 3 + day_offset, hour, minute)
    return SimpleNamespace(title=title, start=base, all_day=all_day)


def make_todo(title="Task", scheduled_at=None, overdue_days=0):
    return SimpleNamespace(title=title, scheduled_at=scheduled_at, overdue_days=overdue_days)


def make_habit(name="Exercise", completed_today=False):
    return SimpleNamespace(name=name, completed_today=completed_today)


TODAY = date(2026, 6, 3)
LOCAL_NOW = datetime(2026, 6, 3, 10, 0)  # 10:00 AM


# ── fmt_time ──────────────────────────────────────────────────────────────────

class TestFmtTime:
    def test_noon(self):
        dt = datetime(2026, 6, 3, 12, 0)
        assert fmt_time(dt) == "12:00 PM"

    def test_midnight(self):
        dt = datetime(2026, 6, 3, 0, 0)
        assert fmt_time(dt) == "12:00 AM"

    def test_drops_leading_zero(self):
        dt = datetime(2026, 6, 3, 9, 30)
        result = fmt_time(dt)
        assert result == "9:30 AM"
        assert not result.startswith("0")

    def test_pm_hour(self):
        dt = datetime(2026, 6, 3, 15, 45)
        assert fmt_time(dt) == "3:45 PM"

    def test_utc_offset_applied(self):
        # UTC+0 datetime adjusted by -300 mins (UTC-5) → subtract 5 hours
        dt = datetime(2026, 6, 3, 17, 0, tzinfo=timezone.utc)
        result = fmt_time(dt, utc_offset_minutes=300)  # getTimezoneOffset returns positive for west
        assert result == "12:00 PM"

    def test_no_offset_naive_datetime(self):
        dt = datetime(2026, 6, 3, 8, 0)
        assert fmt_time(dt, utc_offset_minutes=0) == "8:00 AM"


# ── event_local_date ──────────────────────────────────────────────────────────

class TestEventLocalDate:
    def test_all_day_event_returns_date(self):
        ev = SimpleNamespace(all_day=True, start=date(2026, 6, 5))
        assert event_local_date(ev, 0) == date(2026, 6, 5)

    def test_all_day_event_with_datetime_start(self):
        ev = SimpleNamespace(all_day=True, start=datetime(2026, 6, 5, 0, 0))
        assert event_local_date(ev, 0) == date(2026, 6, 5)

    def test_timed_event_no_offset(self):
        ev = SimpleNamespace(all_day=False, start=datetime(2026, 6, 3, 14, 0))
        assert event_local_date(ev, 0) == date(2026, 6, 3)

    def test_timed_event_crosses_day_with_offset(self):
        # Event at 2:00 AM UTC, UTC-5 (offset=300) → previous day locally
        ev = SimpleNamespace(all_day=False, start=datetime(2026, 6, 4, 2, 0))
        result = event_local_date(ev, 300)
        assert result == date(2026, 6, 3)


# ── build_today_context ───────────────────────────────────────────────────────

class TestBuildTodayContext:
    def test_includes_current_time(self):
        result = build_today_context([], [], TODAY, local_now=LOCAL_NOW)
        assert "10:00 AM" in result
        assert "Wednesday, June 03, 2026" in result

    def test_no_content_shows_nothing_remaining(self):
        result = build_today_context([], [], TODAY, local_now=LOCAL_NOW)
        assert "Nothing remaining" in result

    def test_pending_habit_included(self):
        habits = [make_habit("Meditate", completed_today=False)]
        result = build_today_context([], [], TODAY, habits=habits, local_now=LOCAL_NOW)
        assert "Meditate" in result
        assert "Habits not yet done" in result

    def test_completed_habit_excluded(self):
        habits = [make_habit("Exercise", completed_today=True)]
        result = build_today_context([], [], TODAY, habits=habits, local_now=LOCAL_NOW)
        assert "Exercise" not in result

    def test_todo_appears(self):
        todos = [make_todo("Buy groceries")]
        result = build_today_context(todos, [], TODAY, local_now=LOCAL_NOW)
        assert "Buy groceries" in result
        assert "Tasks for today" in result

    def test_overdue_todo_labeled(self):
        todos = [make_todo("Old task", overdue_days=3)]
        result = build_today_context(todos, [], TODAY, local_now=LOCAL_NOW)
        assert "OVERDUE" in result
        assert "3 days" in result

    def test_overdue_single_day_grammar(self):
        todos = [make_todo("Almost task", overdue_days=1)]
        result = build_today_context(todos, [], TODAY, local_now=LOCAL_NOW)
        assert "1 day]" in result

    def test_future_event_included(self):
        ev = make_event("Team standup", hour=14)
        result = build_today_context([], [ev], TODAY, local_now=LOCAL_NOW)
        assert "Team standup" in result
        assert "Upcoming events" in result

    def test_past_event_excluded(self):
        # Event at 8 AM, local_now is 10 AM
        ev = make_event("Morning yoga", hour=8)
        result = build_today_context([], [ev], TODAY, local_now=LOCAL_NOW)
        assert "Morning yoga" not in result

    def test_all_day_event_included(self):
        ev = SimpleNamespace(title="Holiday", all_day=True, start=datetime(2026, 6, 3, 0, 0))
        result = build_today_context([], [ev], TODAY, local_now=LOCAL_NOW)
        assert "Holiday" in result
        assert "all day" in result

    def test_weather_included(self):
        weather = {"description": "sunny", "high": 80, "low": 60, "umbrella": False, "snow": False, "cold": False, "windy": False}
        result = build_today_context([], [], TODAY, weather=weather, local_now=LOCAL_NOW)
        assert "Weather" in result
        assert "sunny" in result
        assert "80" in result

    def test_weather_umbrella_action(self):
        weather = {"description": "rain", "high": 65, "low": 50, "umbrella": True, "snow": False, "cold": False, "windy": False}
        result = build_today_context([], [], TODAY, weather=weather, local_now=LOCAL_NOW)
        assert "umbrella" in result

    def test_observations_included(self):
        result = build_today_context([], [], TODAY, observations="You skip Mondays.", local_now=LOCAL_NOW)
        assert "Patterns" in result
        assert "skip Mondays" in result

    def test_eng_prs_included(self):
        result = build_today_context([], [], TODAY, eng_prs=["pr1", "pr2"], local_now=LOCAL_NOW)
        assert "2" in result
        assert "GitHub PRs" in result

    def test_health_context_included(self):
        result = build_today_context([], [], TODAY, health_context="Steps: 8000", local_now=LOCAL_NOW)
        assert "Steps: 8000" in result

    def test_recurring_event_tagged(self):
        ev1 = make_event("Standup", hour=14)
        ev2 = make_event("Standup", hour=14, day_offset=1)  # same title, different day
        # All cal events has both; today's events has ev1
        result = build_today_context([], [ev1], TODAY, all_cal_events=[ev1, ev2], local_now=LOCAL_NOW)
        assert "recurring" in result


# ── build_week_context ────────────────────────────────────────────────────────

class TestBuildWeekContext:
    def test_returns_none_when_empty(self):
        assert build_week_context([], [], TODAY) is None

    def test_future_event_included(self):
        ev = make_event("Doctor appointment", hour=10, day_offset=2)
        result = build_week_context([], [ev], TODAY)
        assert result is not None
        assert "Doctor appointment" in result

    def test_today_event_excluded(self):
        ev = make_event("Today's meeting", hour=10, day_offset=0)
        result = build_week_context([], [ev], TODAY)
        assert result is None or "Today's meeting" not in (result or "")

    def test_past_event_excluded(self):
        ev = make_event("Yesterday meeting", hour=10, day_offset=-1)
        result = build_week_context([], [ev], TODAY)
        assert result is None or "Yesterday meeting" not in (result or "")

    def test_unscheduled_todo_included(self):
        todos = [make_todo("Write report")]
        result = build_week_context(todos, [], TODAY)
        assert result is not None
        assert "Write report" in result
        assert "No specific day" in result

    def test_scheduled_future_todo_grouped_by_day(self):
        sched = datetime(2026, 6, 5, 9, 0)
        todos = [make_todo("Friday task", scheduled_at=sched)]
        result = build_week_context(todos, [], TODAY)
        assert result is not None
        assert "Friday task" in result
        assert "June 05" in result

    def test_recurring_events_grouped(self):
        ev1 = make_event("Standup", hour=9, day_offset=1)
        ev2 = make_event("Standup", hour=9, day_offset=2)
        result = build_week_context([], [ev1, ev2], TODAY)
        assert result is not None
        assert "Standup" in result
        assert "Recurring" in result

    def test_recurring_events_not_duplicated_per_day(self):
        ev1 = make_event("Standup", hour=9, day_offset=1)
        ev2 = make_event("Standup", hour=9, day_offset=2)
        result = build_week_context([], [ev1, ev2], TODAY)
        # "Standup" should appear only once in the output (in the recurring block)
        assert result.count("Standup") == 1

    def test_eng_issues_included(self):
        # eng_issues alone triggers the early-return guard; add an event to get output
        ev = make_event("Planning", hour=10, day_offset=1)
        result = build_week_context([], [ev], TODAY, eng_issues=["issue1", "issue2", "issue3"])
        assert result is not None
        assert "3" in result
        assert "GitHub issues" in result

    def test_week_header_contains_tomorrow(self):
        ev = make_event("Meeting", hour=10, day_offset=1)
        result = build_week_context([], [ev], TODAY)
        assert "June 04" in result  # tomorrow = June 4


# ── compute_observations ──────────────────────────────────────────────────────

class TestComputeObservations:
    """Tests for compute_observations use an in-memory SQLite DB."""

    @pytest.fixture
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        import models

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()
        models.Base.metadata.drop_all(bind=engine)

    def test_no_habits_returns_none(self, db):
        result = compute_observations(db, TODAY)
        assert result is None

    def test_young_habit_ignored(self, db):
        import models
        habit = models.Habit(
            name="New habit",
            archived=False,
            created_at=datetime.now() - timedelta(days=5),
        )
        db.add(habit)
        db.commit()
        result = compute_observations(db, TODAY)
        assert result is None

    def test_low_completion_rate_reported(self, db):
        import models
        # Use TODAY as reference so dates are consistent with compute_observations(db, TODAY)
        created = datetime(TODAY.year, TODAY.month, TODAY.day) - timedelta(days=30)
        habit = models.Habit(name="Floss", archived=False, created_at=created)
        db.add(habit)
        db.commit()
        # Add only 5 completions in 30 days → ~17% rate (< 50%)
        for i in range(5):
            db.add(models.HabitCompletion(
                habit_id=habit.id,
                date=(TODAY - timedelta(days=i + 1)).isoformat(),
            ))
        db.commit()
        result = compute_observations(db, TODAY)
        assert result is not None
        assert "Floss" in result
        assert "%" in result

    def test_overdue_tasks_reported(self, db):
        import models
        # scheduled_at must be before TODAY (2026-06-03 00:00) to count as overdue
        today_dt = datetime(TODAY.year, TODAY.month, TODAY.day)
        for i in range(3):
            db.add(models.Card(
                title=f"Overdue task {i}",
                completed=False,
                scheduled_at=today_dt - timedelta(days=i + 2),
            ))
        db.commit()
        result = compute_observations(db, TODAY)
        assert result is not None
        assert "overdue" in result.lower()

    def test_fewer_than_3_overdue_not_reported(self, db):
        import models
        for i in range(2):
            db.add(models.Card(
                title=f"Overdue {i}",
                completed=False,
                scheduled_at=datetime.now() - timedelta(days=2),
            ))
        db.commit()
        result = compute_observations(db, TODAY)
        # Only overdue check — no habits → should be None or not mention overdue
        if result:
            assert "overdue" not in result.lower()
