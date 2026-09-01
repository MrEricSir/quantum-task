"""
Unit tests for streak.py -- habit-streak recomputation and trip-day awareness.

Moved out of test_withings.py (where streak coverage originally lived, growing out of
the Withings auto-check-habits integration) into its own file once "trip mode" added a
second, substantially bigger axis of behavior: streak.py's job is no longer just
"walk day by day and count consecutive completions," it's also "treat some days as
gaps to skip rather than breaks." See PRODUCT_NOTES.md's "Trip mode" entry.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from streak import recompute_from, recompute_all, get_current_streak, get_trip_date_set


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _add_completions(db, habit_id: int, *date_strs: str) -> None:
    for d in date_strs:
        db.add(models.HabitCompletion(habit_id=habit_id, date=d))
    db.flush()


def _streak_entry(db, habit_id: int, date_str: str):
    return db.query(models.HabitStreakDay).filter_by(
        habit_id=habit_id, date=date_str
    ).first()


def _add_trip(db, start: str, end: str | None) -> models.Trip:
    t = models.Trip(start_date=start, end_date=end)
    db.add(t)
    db.flush()
    return t


class TestStreakComputation:
    """Baseline behavior, unaffected by trips -- regression guard for the rewrite."""

    def test_single_completion(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        _add_completions(db, h.id, "2026-06-20")
        recompute_from(db, h.id, today, today=today)
        e = _streak_entry(db, h.id, "2026-06-20")
        assert e is not None and e.streak == 1

    def test_consecutive_days_build_streak(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        _add_completions(db, h.id, "2026-06-18", "2026-06-19", "2026-06-20")
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        assert _streak_entry(db, h.id, "2026-06-18").streak == 1
        assert _streak_entry(db, h.id, "2026-06-19").streak == 2
        assert _streak_entry(db, h.id, "2026-06-20").streak == 3

    def test_gap_resets_streak(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        # Complete Jun 18 and Jun 20 but NOT Jun 19
        _add_completions(db, h.id, "2026-06-18", "2026-06-20")
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        assert _streak_entry(db, h.id, "2026-06-18").streak == 1
        assert _streak_entry(db, h.id, "2026-06-19") is None  # not completed
        assert _streak_entry(db, h.id, "2026-06-20").streak == 1  # reset after gap

    def test_only_completed_days_stored(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        _add_completions(db, h.id, "2026-06-18", "2026-06-20")
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        all_entries = db.query(models.HabitStreakDay).filter_by(habit_id=h.id).all()
        dates = {e.date for e in all_entries}
        assert dates == {"2026-06-18", "2026-06-20"}

    def test_seeds_streak_from_prior_entry(self, db):
        """recompute_from continues an existing streak correctly."""
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        _add_completions(db, h.id, "2026-06-18", "2026-06-19")
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        assert _streak_entry(db, h.id, "2026-06-19").streak == 2

        _add_completions(db, h.id, "2026-06-20")
        recompute_from(db, h.id, date(2026, 6, 20), today=today)
        assert _streak_entry(db, h.id, "2026-06-20").streak == 3  # continues from 2

    def test_retroactive_edit_propagates_forward(self, db):
        """Inserting a completion in the past and recomputing from that date
        correctly updates all subsequent entries."""
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        _add_completions(db, h.id, "2026-06-18", "2026-06-20")
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        assert _streak_entry(db, h.id, "2026-06-20").streak == 1

        _add_completions(db, h.id, "2026-06-19")
        recompute_from(db, h.id, date(2026, 6, 19), today=today)
        assert _streak_entry(db, h.id, "2026-06-19").streak == 2
        assert _streak_entry(db, h.id, "2026-06-20").streak == 3

    def test_recompute_all_full_rebuild(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        _add_completions(db, h.id, "2026-06-18", "2026-06-19", "2026-06-20")
        recompute_all(db, h.id, today=today)
        db.flush()
        assert _streak_entry(db, h.id, "2026-06-18").streak == 1
        assert _streak_entry(db, h.id, "2026-06-20").streak == 3

    def test_recompute_all_no_completions(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        recompute_all(db, h.id, today=date(2026, 6, 20))  # should not raise
        db.flush()
        assert db.query(models.HabitStreakDay).filter_by(habit_id=h.id).count() == 0

    def test_get_streak_when_completed_today(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        _add_completions(db, h.id, "2026-06-18", "2026-06-19", "2026-06-20")
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        assert get_current_streak(db, h.id, today) == 3

    def test_get_streak_not_completed_today_uses_yesterday(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        _add_completions(db, h.id, "2026-06-18", "2026-06-19")
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        # Today (Jun 20) not completed — streak still alive via yesterday
        assert get_current_streak(db, h.id, date(2026, 6, 20)) == 2

    def test_get_streak_zero_when_no_history(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        assert get_current_streak(db, h.id, date(2026, 6, 20)) == 0

    def test_get_streak_zero_after_two_day_gap(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 20)
        _add_completions(db, h.id, "2026-06-17", "2026-06-18")
        recompute_from(db, h.id, date(2026, 6, 17), today=today)
        # Two days later (Jun 20) — yesterday (Jun 19) has no entry, not a trip day
        assert get_current_streak(db, h.id, date(2026, 6, 20)) == 0


class TestTripDateSet:
    def test_active_trip_bounded_at_today(self, db):
        _add_trip(db, "2026-06-18", None)
        days = get_trip_date_set(db, date(2026, 6, 20))
        assert days == {"2026-06-18", "2026-06-19", "2026-06-20"}

    def test_closed_trip_uses_its_own_end_date(self, db):
        _add_trip(db, "2026-06-18", "2026-06-19")
        days = get_trip_date_set(db, date(2026, 6, 25))
        assert days == {"2026-06-18", "2026-06-19"}

    def test_no_trips_is_empty_set(self, db):
        assert get_trip_date_set(db, date(2026, 6, 20)) == set()


class TestTripAwareStreaks:
    """The core behavior this feature adds: trip days are a gap to skip, not a break --
    in either direction (missed or completed)."""

    def test_missed_trip_day_does_not_reset_streak(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        _add_trip(db, "2026-06-19", "2026-06-23")  # 5-day trip, habit untouched
        _add_completions(db, h.id, "2026-06-18", "2026-06-24")
        today = date(2026, 6, 24)
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        assert _streak_entry(db, h.id, "2026-06-18").streak == 1
        # No rows for the missed trip days
        for d in ["2026-06-19", "2026-06-20", "2026-06-21", "2026-06-22", "2026-06-23"]:
            assert _streak_entry(db, h.id, d) is None
        # Streak continues from 1 -> 2 on return, not reset to 1
        assert _streak_entry(db, h.id, "2026-06-24").streak == 2

    def test_completed_trip_day_gets_a_row_but_does_not_advance_streak(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        _add_trip(db, "2026-06-19", "2026-06-20")
        _add_completions(db, h.id, "2026-06-18", "2026-06-19", "2026-06-21")
        today = date(2026, 6, 21)
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        assert _streak_entry(db, h.id, "2026-06-18").streak == 1
        # Completed during the trip -- row exists (heatmap accuracy) but frozen at 1
        assert _streak_entry(db, h.id, "2026-06-19").streak == 1
        # Back to normal -- continues from the frozen value, 1 -> 2
        assert _streak_entry(db, h.id, "2026-06-21").streak == 2

    def test_seed_walks_back_across_a_multi_day_trip_gap(self, db):
        """recompute_from called with from_date == today (the real-world call pattern from
        routers/habits.py) must still find the pre-trip streak, not just look one day back."""
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        _add_trip(db, "2026-06-15", "2026-06-20")  # 6-day trip
        _add_completions(db, h.id, "2026-06-10", "2026-06-11", "2026-06-12", "2026-06-13", "2026-06-14")
        pre_trip_today = date(2026, 6, 14)
        recompute_from(db, h.id, date(2026, 6, 10), today=pre_trip_today)
        assert _streak_entry(db, h.id, "2026-06-14").streak == 5

        # First day back, single-day recompute exactly like the real check-off call site
        _add_completions(db, h.id, "2026-06-21")
        back_today = date(2026, 6, 21)
        recompute_from(db, h.id, back_today, today=back_today)
        assert _streak_entry(db, h.id, "2026-06-21").streak == 6

    def test_get_current_streak_mid_trip_before_checkin_shows_frozen_value(self, db):
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        _add_completions(db, h.id, "2026-06-10", "2026-06-11")
        recompute_from(db, h.id, date(2026, 6, 10), today=date(2026, 6, 11))
        _add_trip(db, "2026-06-12", None)  # still-active trip, started the day after
        # Mid-trip, nothing checked off yet today -- streak should read as the frozen
        # pre-trip value (2), not drop to 0.
        assert get_current_streak(db, h.id, date(2026, 6, 13)) == 2

    def test_retroactive_trip_creation_unfreezes_a_previously_broken_streak(self, db):
        """A gap that looked like a real break gets reinterpreted once a trip is added
        covering it, after a full recompute (as routers/trip.py's mutations trigger)."""
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        today = date(2026, 6, 21)
        _add_completions(db, h.id, "2026-06-18", "2026-06-21")
        recompute_all(db, h.id, today=today)
        assert _streak_entry(db, h.id, "2026-06-21").streak == 1  # looked like a real break

        _add_trip(db, "2026-06-19", "2026-06-20")
        recompute_all(db, h.id, today=today)
        assert _streak_entry(db, h.id, "2026-06-21").streak == 2  # now recognized as protected

    def test_non_trip_gap_still_breaks_the_streak(self, db):
        """Regression guard: a real gap with no trip covering it behaves exactly as before."""
        h = models.Habit(name="Walk"); db.add(h); db.flush()
        _add_trip(db, "2026-07-01", "2026-07-05")  # unrelated trip, doesn't cover the gap
        today = date(2026, 6, 21)
        _add_completions(db, h.id, "2026-06-18", "2026-06-21")
        recompute_from(db, h.id, date(2026, 6, 18), today=today)
        assert _streak_entry(db, h.id, "2026-06-21").streak == 1
