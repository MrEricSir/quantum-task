"""Trip retrospective generation -- the content logic behind the "welcome back" message.

Modeled directly on telegram/scheduler.py's generate_weekly_review: takes plain values
(not an ORM instance bound to another session) so it's equally callable from a
request-scoped db session (trip/router.py's POST /api/trip/{id}/end, for the immediate
send) and from the scheduler's own session (telegram/scheduler.py's
check_trip_retrospective backstop, for a retry if the immediate send failed).
"""
from datetime import date, timedelta

import models
from database import SessionLocal


def generate_trip_retrospective(start_date: str, end_date: str, name: str | None, tz_offset: int) -> str | None:
    """Build the trip retrospective message text: tasks completed, per-habit activity
    during the trip (from real HabitCompletion rows, unaffected by streak freezing) plus
    confirmation streaks were preserved, and health data logged while away.

    Returns None on failure so callers can tell "nothing to report" apart from
    "generation broke" -- same convention as generate_weekly_review.
    """
    from streak import get_current_streak
    from deps import llm_client, LLM_MODEL, reasoning_kwargs

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    try:
        with SessionLocal() as db:
            completed_cards = db.query(models.Card).filter(
                models.Card.completed == True,  # noqa: E712
                models.Card.completed_at.isnot(None),
            ).all()
            completed_count = sum(
                1 for c in completed_cards
                if start <= (c.completed_at.replace(tzinfo=None) - timedelta(minutes=tz_offset)).date() <= end
            )

            habits = db.query(models.Habit).filter_by(archived=False).order_by(models.Habit.id).all()
            habit_lines = []
            for h in habits:
                n = db.query(models.HabitCompletion).filter(
                    models.HabitCompletion.habit_id == h.id,
                    models.HabitCompletion.date >= start.isoformat(),
                    models.HabitCompletion.date <= end.isoformat(),
                ).count()
                if n == 0:
                    continue
                streak = get_current_streak(db, h.id, end)
                habit_lines.append(f"{h.name}: checked off {n} day{'s' if n != 1 else ''} while traveling")
            streak_note = (
                "Streaks kept up with a habit during the trip were preserved and are not broken."
                if habit_lines else
                "No habits were checked off during the trip, but none of the streaks were broken either "
                "-- trip days don't count against them."
            )

            measurement_count = db.query(models.WithingsMeasurement).filter(
                models.WithingsMeasurement.date >= start.isoformat(),
                models.WithingsMeasurement.date <= end.isoformat(),
            ).count()
            food_entries = db.query(models.FoodEntry).all()
            food_count = sum(
                1 for f in food_entries
                if start <= (f.consumed_at.replace(tzinfo=None) - timedelta(minutes=tz_offset)).date() <= end
            )
            workout_entries = db.query(models.WorkoutEntry).all()
            workout_count = sum(
                1 for w in workout_entries
                if start <= (w.logged_at.replace(tzinfo=None) - timedelta(minutes=tz_offset)).date() <= end
            )

        trip_label = f"{name} trip" if name else "trip"
        ctx_lines = [f"{trip_label.capitalize()}: {start.strftime('%b %-d')} - {end.strftime('%b %-d, %Y')}"]
        ctx_lines.append(f"Tasks completed while away: {completed_count}")
        if habit_lines:
            ctx_lines.append("Habit activity during the trip:")
            ctx_lines.extend(f"  - {line}" for line in habit_lines)
        ctx_lines.append(streak_note)
        health_bits = []
        if measurement_count:
            health_bits.append(f"{measurement_count} measurement{'s' if measurement_count != 1 else ''}")
        if food_count:
            health_bits.append(f"{food_count} food log{'s' if food_count != 1 else ''}")
        if workout_count:
            health_bits.append(f"{workout_count} workout{'s' if workout_count != 1 else ''}")
        if health_bits:
            ctx_lines.append(f"Health data logged while away: {', '.join(health_bits)}")

        system = (
            "You write a short, warm \"welcome back from your trip\" message for a personal "
            "productivity and health app, sent over Telegram. Summarize the trip data given "
            "below in 2-4 short sentences -- note what got done while away, reassure the user "
            "their habit streaks weren't broken by the trip, and end on an easy, low-pressure "
            "note about getting back into routine. Do not invent any numbers not given below. "
            "No lists, no headers, just warm, direct prose."
        )
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(ctx_lines)},
            ],
            timeout=20,
            temperature=0.4,
            **reasoning_kwargs(),
        )
        body = resp.choices[0].message.content.strip()
        title = f"Welcome back from {name}" if name else "Welcome back"
        header = f"<b>✈️ {title} — {start.strftime('%b %-d')} to {end.strftime('%b %-d')}</b>\n\n"
        return header + body
    except Exception as e:
        print(f"[trip] retrospective generation error: {e}")
        return None
