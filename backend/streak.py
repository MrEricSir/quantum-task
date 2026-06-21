"""
Streak computation for habits.

The habit_streak_days table stores one row per (habit_id, date) only for
days on which the habit was completed.  The `streak` value is the number of
consecutive completed days up to and including that date.

Only completed days are stored; a missing row means the day was not completed.

Public API
----------
recompute_from(db, habit_id, from_date)
    Recompute streak entries from `from_date` through today.  Call this
    whenever a HabitCompletion is added or removed.

recompute_all(db, habit_id)
    Full rebuild for a single habit from its first ever completion.
    Use for initial population or recovery.

recompute_all_habits(db)
    Rebuild every habit.  Used once at startup to populate the table.

get_current_streak(db, habit_id, today)
    Read the current streak value from the table (does not recompute).
"""

from datetime import date, timedelta

import models
from database import SessionLocal


# ── Core computation ──────────────────────────────────────────────────────────

def recompute_from(db, habit_id: int, from_date: date) -> None:
    """Recompute streak entries for `habit_id` from `from_date` through today.

    Algorithm:
    1. Seed the running streak counter from the entry for from_date - 1
       (so we correctly continue an existing streak).
    2. Delete all existing entries from from_date → today for this habit.
    3. Bulk-fetch all completions in that range in a single query.
    4. Walk day-by-day; insert a row only for completed days.
    """
    today = date.today()
    if from_date > today:
        return

    # Seed streak from the day before the recompute window.
    prev = db.query(models.HabitStreakDay).filter(
        models.HabitStreakDay.habit_id == habit_id,
        models.HabitStreakDay.date == (from_date - timedelta(days=1)).isoformat(),
    ).first()
    running = prev.streak if prev else 0

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
        if date_str in done_dates:
            running += 1
            rows.append(models.HabitStreakDay(
                habit_id=habit_id,
                date=date_str,
                streak=running,
            ))
        else:
            running = 0
        current += timedelta(days=1)

    if rows:
        db.bulk_save_objects(rows)
    db.flush()


def recompute_all(db, habit_id: int) -> None:
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
        recompute_from(db, habit_id, date.fromisoformat(first.date))


def recompute_all_habits(db) -> None:
    """Rebuild streak entries for every habit (used at startup)."""
    habits = db.query(models.Habit).all()
    for habit in habits:
        recompute_all(db, habit.id)
    db.commit()


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_current_streak(db, habit_id: int, today: date) -> int:
    """Return the current streak as of `today`.

    If the habit was completed today, returns today's streak value.
    If not completed today, returns yesterday's streak value (the streak
    is still "alive" until midnight — same behaviour as before).
    """
    entry = db.query(models.HabitStreakDay).filter_by(
        habit_id=habit_id, date=today.isoformat()
    ).first()
    if entry:
        return entry.streak

    yesterday_entry = db.query(models.HabitStreakDay).filter_by(
        habit_id=habit_id, date=(today - timedelta(days=1)).isoformat()
    ).first()
    return yesterday_entry.streak if yesterday_entry else 0
