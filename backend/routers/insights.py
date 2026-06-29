"""
Proactive intelligence: surface stuck tasks and habit trend alerts.

GET /api/insights  — returns a list of insight objects, each with an LLM-generated
                     short description and the subject (card or habit) for action.

A task is "stuck" when it has been in the Today section for 3+ days without
being completed or snoozed.

A habit is "trending low" when it was completed fewer than 4 of the past 7 days
and is at least 7 days old.

Snoozed cards are suppressed until their snoozed_until date; the waiting_reason
is stored on the card itself and displayed as a badge on the board.
"""

import json
from datetime import date, datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db, llm_client, LLM_MODEL, local_date

router = APIRouter()

_STUCK_DAYS = 3  # days in Today before a task is considered stuck

_SYSTEM_PROMPT = """\
You generate brief, actionable insights about a user's task and habit patterns.
For each pattern, write ONE short sentence (max 12 words) that names the issue and suggests a next step.
Be direct and specific — avoid filler phrases like "it seems" or "you might want to consider".
Return JSON: {"insights": [{"index": 0, "text": "..."}]}
Preserve the index values exactly as given.\
"""


def _generate_texts(patterns: list[str]) -> list[str]:
    """Call LLM to produce one short sentence per pattern. Falls back to templates."""
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
        return [by_index.get(i, "") for i in range(len(patterns))]
    except Exception:
        return ["" for _ in patterns]


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
        if count < 4:  # fewer than 4/7 days ≈ < 57%
            low_habits.append((habit, count))

    if not stuck_cards and not low_habits:
        return []

    # ── Build pattern strings for LLM ────────────────────────────────────────
    patterns = []
    for card in stuck_cards:
        days = (today - card.created_at.date()).days
        patterns.append(
            f'Task "{card.title}" has been in Today for {days} days without progress'
        )
    for habit, count in low_habits:
        patterns.append(
            f'Habit "{habit.name}" completed only {count}/7 days this past week'
        )

    texts = _generate_texts(patterns)

    # ── Assemble response ─────────────────────────────────────────────────────
    results = []
    idx = 0

    for card in stuck_cards:
        days = (today - card.created_at.date()).days
        text = texts[idx] or f"In Today for {days} days — reschedule, snooze, or archive."
        results.append({
            "type": "stuck_task",
            "text": text,
            "days_stuck": days,
            "card": schemas.Card.model_validate(card).model_dump(mode="json"),
        })
        idx += 1

    for habit, count in low_habits:
        text = texts[idx] or f"Completed {count}/7 days this week — try to build consistency."
        results.append({
            "type": "habit_trend",
            "text": text,
            "completions_last_7": count,
            "habit_id": habit.id,
            "habit_name": habit.name,
        })
        idx += 1

    return results
