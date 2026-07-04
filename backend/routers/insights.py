"""
Proactive intelligence: surface stuck tasks, habit trend alerts, and health goal alerts.

GET /api/insights  — returns a list of insight objects.

Insight types
─────────────
stuck_task   Task in Today for 3+ days, not snoozed. LLM-generated text + reschedule/snooze/archive actions.
habit_trend  Habit completed <4/7 days this week. LLM-generated text, dismissible.
health_trend Weight or fat-ratio trending away from goal, or stalled while above goal. Template text, dismissible.
health_no_data  No measurement logged recently despite a goal being set. Template text, dismissible.
"""

import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db, llm_client, LLM_MODEL, local_date

router = APIRouter()

_STUCK_DAYS = 3   # days in Today before a task is considered stuck
_NO_DATA_DAYS = 3  # days without a measurement before we remind the user
_RESPONSE_CACHE_TTL = 300  # 5 minutes — full response cache keyed by date string
_response_cache: dict[str, tuple[float, list]] = {}

_SYSTEM_PROMPT = """\
You generate brief, actionable insights about a user's task and habit patterns.
For each pattern, write ONE short sentence (max 12 words) that names the issue and suggests a next step.
Be direct and specific — avoid filler phrases like "it seems" or "you might want to consider".
Return JSON: {"insights": [{"index": 0, "text": "..."}]}
Preserve the index values exactly as given.\
"""

# In-memory cache for LLM-generated texts, keyed on a hash of the patterns.
# DB queries always run; only the LLM call is cached.
_text_cache: dict[str, tuple[float, list[str]]] = {}
_TEXT_CACHE_TTL = 3600  # 1 hour — texts are stable until patterns change


def _patterns_hash(patterns: list[str]) -> str:
    return hashlib.md5("\n".join(patterns).encode()).hexdigest()


def _generate_texts(patterns: list[str]) -> list[str]:
    """Return LLM-generated texts, using cache when patterns are unchanged."""
    ph = _patterns_hash(patterns)
    cached = _text_cache.get(ph)
    if cached and time.monotonic() - cached[0] < _TEXT_CACHE_TTL:
        return cached[1]

    numbered = [f"{i}: {p}" for i, p in enumerate(patterns)]
    try:
        client = llm_client()
        response = client.chat.completions.create(
            model=LLM_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(numbered)},
            ],
            max_tokens=400,
        )
        data = json.loads(response.choices[0].message.content)
        by_index = {item["index"]: item["text"] for item in data.get("insights", [])}
        texts = [by_index.get(i, "") for i in range(len(patterns))]
    except Exception:
        texts = ["" for _ in patterns]

    _text_cache[ph] = (time.monotonic(), texts)
    return texts


def _load_health_goals(db: Session) -> dict[str, float]:
    """Return goal values for weight/fat_ratio. Habit-linked goals override standalone."""
    goals: dict[str, float] = {}
    row = db.query(models.AppSetting).filter_by(key="withings_health_goals").first()
    if row:
        try:
            for k, v in json.loads(row.value).items():
                goals[k] = float(v)
        except Exception:
            pass
    for h in db.query(models.Habit).filter(
        models.Habit.withings_metric.isnot(None),
        models.Habit.withings_goal.isnot(None),
        models.Habit.archived == False,   # noqa: E712
    ).all():
        goals[h.withings_metric] = float(h.withings_goal)
    return goals


def _health_insights(db: Session, today: date) -> list[dict]:
    """Surface weight/fat_ratio goal alerts without calling the LLM."""
    goals = _load_health_goals(db)
    results = []
    cutoff = (today - timedelta(days=30)).isoformat()

    for metric in ("weight", "fat_ratio"):
        goal = goals.get(metric)
        if goal is None:
            continue

        label = "weight" if metric == "weight" else "body fat"
        unit = " kg" if metric == "weight" else "%"

        readings = (
            db.query(models.WithingsMeasurement)
            .filter(
                models.WithingsMeasurement.metric == metric,
                models.WithingsMeasurement.date >= cutoff,
            )
            .order_by(models.WithingsMeasurement.date)
            .all()
        )

        # ── No recent data ────────────────────────────────────────────────────
        if not readings:
            # Only remind if there's ever been a measurement (i.e. Withings is connected)
            ever = db.query(models.WithingsMeasurement).filter(
                models.WithingsMeasurement.metric == metric
            ).first()
            if ever:
                results.append({
                    "type": "health_no_data",
                    "text": f"No {label} logged in 30+ days — step on the scale to track your {goal:.1f}{unit} goal.",
                    "metric": metric,
                    "days_since": 30,
                })
            continue

        latest = readings[-1]
        days_since = (today - date.fromisoformat(latest.date)).days

        if days_since > _NO_DATA_DAYS:
            results.append({
                "type": "health_no_data",
                "text": f"No {label} logged in {days_since} days — step on the scale to track your {goal:.1f}{unit} goal.",
                "metric": metric,
                "days_since": days_since,
            })
            continue

        # ── Trend analysis ────────────────────────────────────────────────────
        current = latest.value
        gap = current - goal   # positive = above goal (want to go down), negative = below (want to go up)

        # Within 1 unit of goal — no alert needed
        if abs(gap) <= 1.0:
            continue

        # Need enough data for a meaningful trend
        if len(readings) < 4:
            continue

        mid = len(readings) // 2
        first_avg = sum(r.value for r in readings[:mid]) / mid
        second_avg = sum(r.value for r in readings[mid:]) / (len(readings) - mid)
        delta = second_avg - first_avg   # positive = trending up over the window
        span_days = (date.fromisoformat(readings[-1].date) - date.fromisoformat(readings[0].date)).days

        # "Wrong direction" = trending away from goal
        if gap > 0 and delta > 0.3:
            # Above goal and still going up
            results.append({
                "type": "health_trend",
                "text": f"{label.capitalize()} is trending up (+{abs(delta):.1f}{unit} over {span_days}d) while {abs(gap):.1f}{unit} above your goal.",
                "metric": metric,
                "direction": "worsening",
                "delta": round(delta, 2),
                "gap": round(gap, 2),
            })
        elif gap < 0 and delta < -0.3:
            # Below goal and still going down
            results.append({
                "type": "health_trend",
                "text": f"{label.capitalize()} is trending down ({abs(delta):.1f}{unit} over {span_days}d) while {abs(gap):.1f}{unit} below your goal.",
                "metric": metric,
                "direction": "worsening",
                "delta": round(delta, 2),
                "gap": round(gap, 2),
            })
        elif span_days >= 14 and abs(delta) <= 0.3:
            # No meaningful movement for 2+ weeks while above/below goal
            direction_word = "above" if gap > 0 else "below"
            results.append({
                "type": "health_trend",
                "text": f"{label.capitalize()} hasn't moved in {span_days} days — still {abs(gap):.1f}{unit} {direction_word} your {goal:.1f}{unit} goal.",
                "metric": metric,
                "direction": "stalled",
                "delta": round(delta, 2),
                "gap": round(gap, 2),
            })

    # ── Blood pressure ────────────────────────────────────────────────────────
    sys_readings = (
        db.query(models.WithingsMeasurement)
        .filter(models.WithingsMeasurement.metric == "bp_systolic",
                models.WithingsMeasurement.date >= cutoff)
        .order_by(models.WithingsMeasurement.date)
        .all()
    )
    dia_readings = (
        db.query(models.WithingsMeasurement)
        .filter(models.WithingsMeasurement.metric == "bp_diastolic",
                models.WithingsMeasurement.date >= cutoff)
        .order_by(models.WithingsMeasurement.date)
        .all()
    )

    if not sys_readings and not dia_readings:
        ever_bp = db.query(models.WithingsMeasurement).filter(
            models.WithingsMeasurement.metric == "bp_systolic"
        ).first()
        if ever_bp:
            results.append({
                "type": "health_no_data",
                "text": "No blood pressure reading in 30+ days — take a reading to track cardiovascular health.",
                "metric": "blood_pressure",
                "days_since": 30,
            })
    else:
        sys_by_date = {r.date: r.value for r in sys_readings}
        dia_by_date = {r.date: r.value for r in dia_readings}
        shared = sorted(set(sys_by_date) & set(dia_by_date))
        if shared:
            latest_date = shared[-1]
            sys_val = sys_by_date[latest_date]
            dia_val = dia_by_date[latest_date]
            days_since = (today - date.fromisoformat(latest_date)).days

            if days_since > 7:
                results.append({
                    "type": "health_no_data",
                    "text": f"No blood pressure reading in {days_since} days — take a reading to track cardiovascular health.",
                    "metric": "blood_pressure",
                    "days_since": days_since,
                })
            elif sys_val >= 140 or dia_val >= 90:
                results.append({
                    "type": "health_bp",
                    "text": f"Blood pressure {int(sys_val)}/{int(dia_val)} mmHg is in the Stage 2 high range — consult your doctor.",
                    "metric": "blood_pressure",
                    "systolic": sys_val,
                    "diastolic": dia_val,
                    "stage": 2,
                })
            elif sys_val >= 130 or dia_val >= 80:
                results.append({
                    "type": "health_bp",
                    "text": f"Blood pressure {int(sys_val)}/{int(dia_val)} mmHg is elevated — monitor and consider lifestyle adjustments.",
                    "metric": "blood_pressure",
                    "systolic": sys_val,
                    "diastolic": dia_val,
                    "stage": 1,
                })
            elif sys_val < 90 or dia_val < 60:
                results.append({
                    "type": "health_bp",
                    "text": f"Blood pressure {int(sys_val)}/{int(dia_val)} mmHg is low — stay hydrated and monitor for symptoms.",
                    "metric": "blood_pressure",
                    "systolic": sys_val,
                    "diastolic": dia_val,
                    "stage": 0,
                })

    return results


def _completion_time_insight(db: Session) -> dict | None:
    """Surface the user's peak task-completion window (morning/afternoon/evening).
    Requires 20+ completions to avoid noise."""
    ninety_ago = datetime.now(timezone.utc) - timedelta(days=90)
    completed = (
        db.query(models.Card.completed_at)
        .filter(
            models.Card.completed == True,       # noqa: E712
            models.Card.completed_at >= ninety_ago,
            models.Card.completed_at.isnot(None),
        )
        .all()
    )
    hours = [row.completed_at.hour for row in completed]
    if len(hours) < 20:
        return None

    windows = {
        "morning":   sum(1 for h in hours if 5 <= h < 12),
        "afternoon": sum(1 for h in hours if 12 <= h < 18),
        "evening":   sum(1 for h in hours if 18 <= h <= 23),
    }
    total = sum(windows.values())
    if total < 20:
        return None

    peak = max(windows, key=windows.get)
    if windows[peak] / total < 0.50:
        return None  # no clear dominant window

    texts = {
        "morning":   "You finish most tasks before noon — protect your mornings from meetings.",
        "afternoon": "You do your best completing work in the afternoon — batch tasks 12–6 pm.",
        "evening":   "You tend to finish tasks in the evening — plan a focused end-of-day session.",
    }
    return {
        "type": "completion_pattern",
        "text": texts[peak],
        "peak_window": peak,
        "peak_pct": round(windows[peak] / total, 2),
    }


def invalidate_insights_cache() -> None:
    """Clear the response cache — call after any mutation that affects insight output."""
    _response_cache.clear()


@router.get("/api/insights")
def get_insights(request: Request, db: Session = Depends(get_db)):
    today = local_date(request)
    today_str = today.isoformat()

    # ── Full-response cache (5-minute TTL) ────────────────────────────────────
    cached = _response_cache.get(today_str)
    if cached and time.monotonic() - cached[0] < _RESPONSE_CACHE_TTL:
        return cached[1]

    # ── Stuck tasks ───────────────────────────────────────────────────────────
    # Use today_since when available (accurate entry time); fall back to created_at.
    stuck_cutoff = datetime(
        *(today - timedelta(days=_STUCK_DAYS)).timetuple()[:3],
        tzinfo=timezone.utc,
    )
    stuck_cards: List[models.Card] = (
        db.query(models.Card)
        .filter(
            models.Card.section == "today",
            models.Card.completed == False,       # noqa: E712
            models.Card.archived == False,        # noqa: E712
            or_(
                and_(models.Card.today_since.isnot(None), models.Card.today_since <= stuck_cutoff),
                and_(models.Card.today_since.is_(None),  models.Card.created_at  <= stuck_cutoff),
            ),
            or_(
                models.Card.snoozed_until.is_(None),
                models.Card.snoozed_until < today_str,
            ),
        )
        .order_by(models.Card.created_at)
        .all()
    )

    # ── Low-completion habits ─────────────────────────────────────────────────
    seven_ago = (today - timedelta(days=7)).isoformat()
    habit_cutoff = datetime(
        *(today - timedelta(days=7)).timetuple()[:3],
        tzinfo=timezone.utc,
    )
    habits: List[models.Habit] = (
        db.query(models.Habit)
        .filter(
            models.Habit.archived == False,       # noqa: E712
            models.Habit.withings_metric.is_(None),  # auto-checked habits excluded
            models.Habit.created_at <= habit_cutoff,
        )
        .all()
    )

    low_habits = []
    for habit in habits:
        count = (
            db.query(models.HabitCompletion)
            .filter(
                models.HabitCompletion.habit_id == habit.id,
                models.HabitCompletion.date >= seven_ago,
                models.HabitCompletion.date <= today_str,
            )
            .count()
        )
        if count < 4:
            low_habits.append((habit, count))

    # ── Health goal alerts (template text — no LLM) ───────────────────────────
    health = _health_insights(db, today)

    # ── Completion time pattern (template text — no LLM) ─────────────────────
    pattern = _completion_time_insight(db)

    if not stuck_cards and not low_habits and not health and not pattern:
        _response_cache[today_str] = (time.monotonic(), [])
        return []

    # ── LLM texts for task/habit patterns ────────────────────────────────────
    patterns = []
    for card in stuck_cards:
        entry_date = (card.today_since or card.created_at).date()
        days = (today - entry_date).days
        patterns.append(f'Task "{card.title}" has been in Today for {days} days without progress')
    for habit, count in low_habits:
        patterns.append(f'Habit "{habit.name}" completed only {count}/7 days this past week')

    texts = _generate_texts(patterns) if patterns else []

    # ── Assemble response ─────────────────────────────────────────────────────
    results = []
    idx = 0

    for card in stuck_cards:
        entry_date = (card.today_since or card.created_at).date()
        days = (today - entry_date).days
        text = texts[idx] if idx < len(texts) else ""
        results.append({
            "type": "stuck_task",
            "text": text or f"In Today for {days} days — reschedule, snooze, or archive.",
            "days_stuck": days,
            "card": schemas.Card.model_validate(card).model_dump(mode="json"),
        })
        idx += 1

    for habit, count in low_habits:
        text = texts[idx] if idx < len(texts) else ""
        results.append({
            "type": "habit_trend",
            "text": text or f"Completed {count}/7 days this week — try to build consistency.",
            "completions_last_7": count,
            "habit_id": habit.id,
            "habit_name": habit.name,
        })
        idx += 1

    results.extend(health)
    if pattern:
        results.append(pattern)

    _response_cache[today_str] = (time.monotonic(), results)
    return results
