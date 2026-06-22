"""
Shared health context builder for all AI features (briefing, daily plan, workshop).

Queries the DB directly so the backend owns the full picture — callers don't
need to pass health data from the client.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

import models

_GOALS_KEY = "withings_health_goals"


def _load_standalone_goals(db: "Session") -> dict:
    row = db.query(models.AppSetting).filter_by(key=_GOALS_KEY).first()
    if not row:
        return {}
    try:
        return json.loads(row.value)
    except Exception:
        return {}


def build_health_context(db: "Session", today: date) -> tuple[dict, str | None]:
    """Return (data, context_str).

    data = {
        "today":  { metric: float },   # measurements recorded today
        "recent": { metric: float },   # most recent value (may be older than today)
        "recent_date": { metric: str },# YYYY-MM-DD of most recent measurement
        "goals":  { metric: float },   # goal values (habit-linked takes priority)
    }

    context_str is a compact, LLM-ready text block, or None if no data.
    """
    today_str = today.isoformat()
    cutoff = (today - timedelta(days=30)).isoformat()

    measurements = (
        db.query(models.WithingsMeasurement)
        .filter(models.WithingsMeasurement.date >= cutoff)
        .order_by(models.WithingsMeasurement.date)
        .all()
    )

    today_vals: dict[str, float] = {}
    recent_vals: dict[str, float] = {}
    recent_dates: dict[str, str] = {}

    for m in measurements:
        recent_vals[m.metric] = m.value
        recent_dates[m.metric] = m.date
        if m.date == today_str:
            today_vals[m.metric] = m.value

    # Goals: habit-linked goals take priority over standalone goals
    standalone_goals = _load_standalone_goals(db)
    habit_goals: dict[str, float] = {}
    for h in (
        db.query(models.Habit)
        .filter(
            models.Habit.withings_metric.isnot(None),
            models.Habit.withings_goal.isnot(None),
            models.Habit.archived == False,  # noqa: E712
        )
        .all()
    ):
        habit_goals[h.withings_metric] = h.withings_goal

    goals: dict[str, float | None] = {}
    for metric in ("steps", "fat_ratio", "weight"):
        goals[metric] = habit_goals.get(metric) or standalone_goals.get(metric) or None

    data = {
        "today": today_vals,
        "recent": recent_vals,
        "recent_date": recent_dates,
        "goals": goals,
    }

    if not recent_vals:
        return data, None

    lines = ["Health data:"]
    has_content = False

    # Steps — always use today's value (changes during the day)
    if "steps" in today_vals:
        steps = today_vals["steps"]
        goal = goals.get("steps")
        if goal:
            pct = round(steps / goal * 100)
            remaining = max(0, int(goal - steps))
            rem_note = f", {remaining:,} remaining" if remaining > 0 else " — goal reached"
            lines.append(f"  - Steps: {int(steps):,} / {int(goal):,} ({pct}%{rem_note})")
        else:
            lines.append(f"  - Steps: {int(steps):,}")
        has_content = True

    # Weight — most recent reading (may not be today)
    if "weight" in recent_vals:
        val = recent_vals["weight"]
        mdate = recent_dates.get("weight", today_str)
        date_note = f" (as of {mdate})" if mdate != today_str else ""
        goal = goals.get("weight")
        if goal:
            diff = val - goal
            direction = "above" if diff > 0 else "below"
            lines.append(
                f"  - Weight: {val:.1f} kg, {abs(diff):.1f} kg {direction} goal of {goal:.1f} kg{date_note}"
            )
        else:
            lines.append(f"  - Weight: {val:.1f} kg{date_note}")
        has_content = True

    # Body fat — most recent reading
    if "fat_ratio" in recent_vals:
        val = recent_vals["fat_ratio"]
        mdate = recent_dates.get("fat_ratio", today_str)
        date_note = f" (as of {mdate})" if mdate != today_str else ""
        goal = goals.get("fat_ratio")
        if goal:
            diff = val - goal
            direction = "above" if diff > 0 else "below"
            lines.append(
                f"  - Body fat: {val:.1f}%, {abs(diff):.1f}pp {direction} goal of {goal:.1f}%{date_note}"
            )
        else:
            lines.append(f"  - Body fat: {val:.1f}%{date_note}")
        has_content = True

    return data, "\n".join(lines) if has_content else None
