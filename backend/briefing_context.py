"""
Context-building helpers for the daily briefing.

Separated from briefing.py to keep the router thin.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, time as dt_time

from sqlalchemy.orm import Session

import models


# ── Time / event helpers ──────────────────────────────────────────────────────

def fmt_time(dt: datetime, utc_offset_minutes: int = 0) -> str:
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None) - timedelta(minutes=utc_offset_minutes)
    return dt.strftime("%I:%M %p").lstrip("0")


def event_local_date(e, utc_offset_minutes: int) -> date:
    """Return the calendar event's date in the client's local timezone."""
    if e.all_day:
        return e.start.date() if hasattr(e.start, "date") else e.start
    local_dt = e.start.replace(tzinfo=None) - timedelta(minutes=utc_offset_minutes)
    return local_dt.date()


# ── Observations ──────────────────────────────────────────────────────────────

def compute_observations(db: Session, today: date) -> str | None:
    """Derive plain-text behavioral observations from the last 30 days."""
    cutoff_str = (today - timedelta(days=30)).isoformat()
    cutoff_dt  = datetime.combine(today - timedelta(days=30), dt_time.min)
    lines: list[str] = []

    habits = db.query(models.Habit).filter(models.Habit.archived == False).all()  # noqa: E712
    for habit in habits:
        created = habit.created_at.date() if habit.created_at else today
        days_old = (today - created).days
        if days_old < 14:
            continue

        window = min(30, days_old)
        done_dates = {
            c.date for c in db.query(models.HabitCompletion).filter(
                models.HabitCompletion.habit_id == habit.id,
                models.HabitCompletion.date >= cutoff_str,
            ).all()
        }
        rate = len(done_dates) / window

        if rate < 0.5:
            lines.append(
                f'Habit "{habit.name}" completion: {round(rate * 100)}% over the past {window} days.'
            )
            continue

        by_wd: dict[str, dict] = defaultdict(lambda: {"done": 0, "total": 0})
        for i in range(window):
            d = today - timedelta(days=i + 1)
            wd = d.strftime("%A")
            by_wd[wd]["total"] += 1
            if d.isoformat() in done_dates:
                by_wd[wd]["done"] += 1

        worst_day, worst_rate = None, 1.0
        for wd, counts in by_wd.items():
            if counts["total"] >= 3:
                wd_rate = counts["done"] / counts["total"]
                if wd_rate < (rate - 0.40) and wd_rate < worst_rate:
                    worst_rate, worst_day = wd_rate, wd
        if worst_day:
            lines.append(
                f'You often skip "{habit.name}" on {worst_day}s '
                f'({round(worst_rate * 100)}% vs {round(rate * 100)}% average).'
            )

    overdue = db.query(models.Card).filter(
        models.Card.completed == False,  # noqa: E712
        models.Card.scheduled_at.isnot(None),
        models.Card.scheduled_at < datetime.combine(today, dt_time.min),
    ).count()
    if overdue >= 3:
        lines.append(f"You have {overdue} overdue scheduled tasks.")

    created_n = db.query(models.Card).filter(
        models.Card.created_at >= cutoff_dt,
    ).count()
    completed_n = db.query(models.Card).filter(
        models.Card.completed == True,  # noqa: E712
        models.Card.completed_at.isnot(None),
        models.Card.completed_at >= cutoff_dt,
    ).count()
    if created_n >= 10:
        cr = completed_n / created_n
        if cr < 0.4:
            lines.append(
                f"Task completion rate: {round(cr * 100)}% over the past 30 days "
                f"({completed_n} of {created_n} completed)."
            )

    return "\n".join(lines) if lines else None


# ── Context builders ──────────────────────────────────────────────────────────

def build_today_context(
    todos: list,
    cal_events: list,
    today: date,
    habits: list = None,
    observations: str | None = None,
    utc_offset_minutes: int = 0,
    eng_prs: list = None,
    local_now: datetime | None = None,
    weather: dict | None = None,
    all_cal_events: list = None,
    health_context: str | None = None,
) -> str:
    now = local_now or datetime.now()
    time_str = now.strftime("%-I:%M %p").lstrip("0") if hasattr(now, "strftime") else ""
    lines = [f"Current time: {time_str} on {today.strftime('%A, %B %d, %Y')}"]

    if weather:
        w_line = f"Weather: {weather['description']}, high {weather['high']}°F / low {weather['low']}°F"
        w_actions = []
        if weather.get("umbrella"):
            w_actions.append("rain — advise bringing an umbrella")
        if weather.get("snow"):
            w_actions.append("snow — mention it")
        if weather.get("cold"):
            w_actions.append("cold — advise dressing warmly")
        if weather.get("windy"):
            w_actions.append("very windy — worth mentioning")
        if w_actions:
            w_line += f". Action: {'; '.join(w_actions)}"
        lines.append(w_line)

    recurring_titles: set[str] = set()
    if all_cal_events:
        title_days: dict[str, set] = {}
        for e in all_cal_events:
            day = event_local_date(e, utc_offset_minutes)
            title_days.setdefault(e.title, set()).add(day)
        recurring_titles = {t for t, days in title_days.items() if len(days) >= 2}

    # OOO events are pre-filtered by the caller and never appear here.
    upcoming_events = []
    for e in cal_events:
        if e.all_day:
            upcoming_events.append(e)
        else:
            local_start = e.start.replace(tzinfo=None) - timedelta(minutes=utc_offset_minutes) \
                if e.start.tzinfo else e.start
            if local_start >= now:
                upcoming_events.append(e)

    if upcoming_events:
        lines.append("Upcoming events:")
        for e in upcoming_events:
            recurring_tag = " (recurring)" if e.title in recurring_titles else ""
            if e.all_day:
                lines.append(f"  - {e.title} (all day){recurring_tag}")
            else:
                lines.append(f"  - {e.title} at {fmt_time(e.start, utc_offset_minutes)}{recurring_tag}")

    if todos:
        lines.append("Tasks for today:")
        for t in todos:
            suffix = f" at {fmt_time(t.scheduled_at, utc_offset_minutes)}" if t.scheduled_at else ""
            overdue = f" [OVERDUE by {t.overdue_days} day{'s' if t.overdue_days != 1 else ''}]" if t.overdue_days > 0 else ""
            lines.append(f"  - {t.title}{suffix}{overdue}")

    if eng_prs:
        lines.append(f"GitHub PRs awaiting your review: {len(eng_prs)}")

    pending_habits = [h.name for h in (habits or []) if not h.completed_today]
    if pending_habits:
        lines.append("Habits not yet done today:")
        for name in pending_habits:
            lines.append(f"  - {name}")

    if not upcoming_events and not todos and not eng_prs and not pending_habits:
        lines.append("Nothing remaining on the schedule.")

    if health_context:
        lines.append(health_context)

    if observations:
        lines.append("Patterns (use at most one if genuinely relevant):")
        lines.append(observations)

    return "\n".join(lines)


def build_week_context(
    todos: list,
    cal_events: list,
    today: date,
    utc_offset_minutes: int = 0,
    eng_issues: list = None,
) -> str | None:
    if not todos and not cal_events:
        return None

    recur_groups: dict[tuple, list] = {}
    for e in cal_events:
        start = e.start
        day = event_local_date(e, utc_offset_minutes)
        if day <= today:
            continue
        time_key = "all_day" if e.all_day else fmt_time(start, utc_offset_minutes)
        recur_groups.setdefault((e.title, time_key), []).append((day, start, e.all_day))
    recurring_keys = {k for k, v in recur_groups.items() if len(v) >= 2}

    by_day: dict[date, list[tuple]] = {}
    unscheduled: list[str] = []

    for e in cal_events:
        start = e.start
        day = event_local_date(e, utc_offset_minutes)
        if day <= today:
            continue
        time_key = "all_day" if e.all_day else fmt_time(start, utc_offset_minutes)
        if (e.title, time_key) in recurring_keys:
            continue
        if e.all_day:
            by_day.setdefault(day, []).append((None, f"- {e.title} (all day)"))
        else:
            by_day.setdefault(day, []).append((start, f"- {e.title} at {fmt_time(start, utc_offset_minutes)}"))

    for t in todos:
        if t.scheduled_at:
            day = t.scheduled_at.date()
            if day <= today:
                continue
            by_day.setdefault(day, []).append((t.scheduled_at, f"- {t.title} at {fmt_time(t.scheduled_at, utc_offset_minutes)}"))
        else:
            unscheduled.append(f"- {t.title}")

    if not by_day and not unscheduled and not recurring_keys and not eng_issues:
        return None

    tomorrow = today + timedelta(days=1)
    lines = [f"Week ahead starting {tomorrow.strftime('%A, %B %d, %Y')}:"]

    if recurring_keys:
        lines.append("\nRecurring this week (mention ONCE at the start, never repeat per day):")
        for (title, time_key), occurrences in sorted(recur_groups.items(), key=lambda x: x[0][0]):
            if (title, time_key) not in recurring_keys:
                continue
            day_names = ", ".join(d.strftime("%a") for d, _, _ in sorted(occurrences, key=lambda x: x[0]))
            if time_key == "all_day":
                lines.append(f"  - {title} (all day) — {day_names}")
            else:
                lines.append(f"  - {title} at {time_key} — {day_names}")

    for day in sorted(by_day):
        lines.append(f"\n{day.strftime('%A, %B %d')}:")
        items = sorted(by_day[day], key=lambda x: (x[0] is not None, x[0] or datetime.min))
        for _, text in items:
            lines.append(f"  {text}")

    if unscheduled:
        lines.append("\nNo specific day:")
        for item in unscheduled:
            lines.append(f"  {item}")

    if eng_issues:
        lines.append(f"\nAssigned GitHub issues: {len(eng_issues)}")

    return "\n".join(lines)
