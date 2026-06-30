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
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db, llm_client, LLM_MODEL, local_date

router = APIRouter()

_STUCK_DAYS = 3   # days in Today before a task is considered stuck
_NO_DATA_DAYS = 3  # days without a measurement before we remind the user

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

    return results


@router.get("/api/insights")
def get_insights(request: Request, db: Session = Depends(get_db)):
    today = local_date(request)
    today_str = today.isoformat()

    # ── Stuck tasks ───────────────────────────────────────────────────────────
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
            models.Card.created_at <= stuck_cutoff,
            or_(
                models.Card.snoozed_until == None,   # noqa: E711
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
            models.Habit.withings_metric == None, # noqa: E711 — auto-checked habits excluded
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

    if not stuck_cards and not low_habits and not health:
        return []

    # ── LLM texts for task/habit patterns ────────────────────────────────────
    patterns = []
    for card in stuck_cards:
        days = (today - card.created_at.date()).days
        patterns.append(f'Task "{card.title}" has been in Today for {days} days without progress')
    for habit, count in low_habits:
        patterns.append(f'Habit "{habit.name}" completed only {count}/7 days this past week')

    texts = _generate_texts(patterns) if patterns else []

    # ── Assemble response ─────────────────────────────────────────────────────
    results = []
    idx = 0

    for card in stuck_cards:
        days = (today - card.created_at.date()).days
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
    return results
