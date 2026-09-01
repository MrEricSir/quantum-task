"""
Streak computation for habits.

The habit_streak_days table stores one row per (habit_id, date) only for
days on which the habit was completed.  The `streak` value is the number of
consecutive completed days up to and including that date.

Only completed days are stored; a missing row means the day was not completed
-- EXCEPT for a day that falls inside a Trip (see models.Trip): trip days are
a gap the streak walk skips over rather than a break, so a habit checked off
on a trip day still gets a row (streak accuracy in the 7-day heatmap /
completed_today), but its `streak` value is frozen at whatever it was
entering the trip, and a *missed* trip day gets no row and does not reset the
running count either. This is "keep logging, ignore the day for streak math"
-- both directions.

Public API
----------
recompute_from(db, habit_id, from_date, today=None)
    Recompute streak entries from `from_date` through today.  Call this
    whenever a HabitCompletion is added or removed, or a Trip is
    created/edited/deleted.

recompute_all(db, habit_id, today=None)
    Full rebuild for a single habit from its first ever completion.
    Use for initial population or recovery.

recompute_all_habits(db, today=None)
    Rebuild every habit.  Used once at startup to populate the table, and
    whenever a Trip is created/edited/deleted/ended (cheap at personal-app
    scale; correctness matters far more than shaving this).

get_current_streak(db, habit_id, today)
    Read the current streak value from the table (does not recompute).
"""

from datetime import date, timedelta

import models
from database import SessionLocal


# ── Trip days ──────────────────────────────────────────────────────────────────

def get_trip_date_set(db, today: date) -> set[str]:
    """All "YYYY-MM-DD" dates covered by any Trip, past or active.

    An active trip (end_date is null) is bounded at `today` -- a trip can't cover
    dates that haven't happened yet. Trips are rare and short, so this whole set is
    trivially small even across years of history.
    """
    days: set[str] = set()
    for trip in db.query(models.Trip).all():
        start = date.fromisoformat(trip.start_date)
        end = date.fromisoformat(trip.end_date) if trip.end_date else today
        if end < start:
            continue
        current = start
        while current <= end:
            days.add(current.isoformat())
            current += timedelta(days=1)
    return days


# ── Core computation ──────────────────────────────────────────────────────────

def _streak_at_or_before(db, habit_id: int, d: date, trip_days: set[str]) -> int:
    """Walk backward from `d` (inclusive) for the most recent real streak value,
    treating any date in `trip_days` with no row as a gap to skip rather than a break."""
    while True:
        row = db.query(models.HabitStreakDay).filter_by(
            habit_id=habit_id, date=d.isoformat()
        ).first()
        if row:
            return row.streak
        if d.isoformat() not in trip_days:
            return 0
        d -= timedelta(days=1)


def recompute_from(db, habit_id: int, from_date: date, today: date | None = None) -> None:
    """Recompute streak entries for `habit_id` from `from_date` through today.

    Algorithm:
    1. Seed the running streak counter by walking backward from from_date - 1,
       skipping over trip-day gaps, so a streak correctly resumes across a trip.
    2. Delete all existing entries from from_date -> today for this habit.
    3. Bulk-fetch all completions in that range in a single query.
    4. Walk day-by-day; insert a row only for completed days. On a trip day, the
       running count is frozen either way -- a completion still gets a row (at the
       frozen value) so the heatmap stays accurate, but doesn't advance the count;
       a miss gets no row and doesn't reset the count.
    """
    today = today or date.today()
    if from_date > today:
        return

    trip_days = get_trip_date_set(db, today)

    running = _streak_at_or_before(db, habit_id, from_date - timedelta(days=1), trip_days)

    # Clear the window we're about to rewrite.
    db.query(models.HabitStreakDay).filter(
        models.HabitStreakDay.habit_id == habit_id,
        models.HabitStreakDay.date >= from_date.isoformat(),
        models.HabitStreakDay.date <= today.isoformat(),
    ).delete(synchronize_session=False)

    # Fetch all completions in the window (one query).
    completions = db.query(models.HabitCompletion).filter(
        models.HabitCompletion.habit_id == habit_id,
        models.HabitCompletion.date >= from_date.isoformat(),
        models.HabitCompletion.date <= today.isoformat(),
    ).all()
    done_dates = {c.date for c in completions}

    # Walk day-by-day, build new rows for completed days only.
    rows = []
    current = from_date
    while current <= today:
        date_str = current.isoformat()
        is_trip_day = date_str in trip_days
        if date_str in done_dates:
            if not is_trip_day:
                running += 1
            rows.append(models.HabitStreakDay(
                habit_id=habit_id,
                date=date_str,
                streak=running,
            ))
        elif not is_trip_day:
            running = 0
        # trip day, not completed: no row, running left untouched
        current += timedelta(days=1)

    if rows:
        db.bulk_save_objects(rows)
    db.flush()


def recompute_all(db, habit_id: int, today: date | None = None) -> None:
    """Full rebuild for `habit_id` from its first ever completion."""
    first = (
        db.query(models.HabitCompletion)
        .filter_by(habit_id=habit_id)
        .order_by(models.HabitCompletion.date)
        .first()
    )

    # Clear everything for this habit first.
    db.query(models.HabitStreakDay).filter_by(habit_id=habit_id).delete(
        synchronize_session=False
    )
    db.flush()

    if first:
        recompute_from(db, habit_id, date.fromisoformat(first.date), today=today)


def recompute_all_habits(db, today: date | None = None) -> None:
    """Rebuild streak entries for every habit (used at startup, and whenever a Trip
    is created/edited/deleted/ended so historical streaks reflect it immediately)."""
    habits = db.query(models.Habit).all()
    for habit in habits:
        recompute_all(db, habit.id, today=today)
    db.commit()


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_current_streak(db, habit_id: int, today: date) -> int:
    """Return the current streak as of `today`.

    If the habit was completed today, returns today's streak value. If not (yet)
    completed today, falls back one day -- the streak is still "alive" until
    midnight, same as before trip mode existed -- and from there on, walks
    backward through any trip-day gap to the most recent real value (so mid-trip,
    before that day's check-in, the streak still reads as its frozen pre-trip
    value rather than 0). A genuine non-trip miss still reads as 0.
    """
    row = db.query(models.HabitStreakDay).filter_by(
        habit_id=habit_id, date=today.isoformat()
    ).first()
    if row:
        return row.streak
    trip_days = get_trip_date_set(db, today)
    return _streak_at_or_before(db, habit_id, today - timedelta(days=1), trip_days)
