"""
Food and drink log.

POST   /api/food            – parse raw text with LLM, store entry
GET    /api/food?date=...   – entries for a date (YYYY-MM-DD; defaults to today)
DELETE /api/food/{id}       – remove an entry
"""

import json
from datetime import date, datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
from deps import get_db, llm_client, LLM_MODEL, local_date, utc_offset_minutes

router = APIRouter()

_PARSE_SYSTEM = """\
You parse food and drink log entries into structured data. \
Respond with ONLY valid JSON (no markdown, no explanation).

{{
  "name":      "concise name of what was consumed, e.g. 'donut', 'coffee with oat milk', 'chicken salad'",
  "category":  "food" | "drink",
  "meal_type": "breakfast" | "lunch" | "dinner" | "snack" | "drink",
  "notes":     "1-2 sentence honest nutritional assessment — be specific, not preachy",
  "quality":   integer 1-10 (10 = highly nutritious whole foods; 1 = pure junk with no redeeming value),
  "calories":  integer estimated kcal — best estimate for a typical serving; null only if truly impossible
}}

meal_type rules:
- Use "drink" for any beverage (coffee, tea, water, juice, alcohol)
- Use the local hour of consumption (provided below) as the primary signal for meal classification:
    5–10  → breakfast
    11–13 → lunch
    17–21 → dinner
    outside those windows → snack (unless the text clearly says otherwise)
- Text context overrides time (e.g. "late lunch" at 3pm → lunch)
- "about to" / "going to" still counts as the current time

assumption rule:
- Unless milk, cream, sugar, syrup, honey, or another addition is explicitly
  mentioned, assume the plain/black/unsweetened form (e.g. "tea" and "coffee"
  mean plain tea and black coffee, not a version with added milk or sugar)

quality examples:
- leafy salad, grilled salmon → 9
- black coffee, plain tea (no milk/sugar) → 8
- oatmeal with fruit → 8
- banana → 7
- coffee with milk → 6
- white rice with vegetables → 6
- pizza slice → 4
- donut → 3
- bag of chips → 3
- energy drink → 2

calories examples:
- black coffee → 5
- plain tea → 2
- coffee with oat milk → 60
- banana → 90
- donut → 300
- pizza slice → 285
- chicken salad (large) → 450
- bag of chips (regular) → 150

Local hour of consumption: {hour}
"""


def _parse_food(raw: str, hour: int = 12) -> dict:
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _PARSE_SYSTEM.format(hour=hour)},
                {"role": "user",   "content": raw},
            ],
            max_tokens=200,
        )
        data = json.loads(resp.choices[0].message.content.strip())
        name      = str(data.get("name", raw[:80]))
        category  = data.get("category", "food")
        if category not in ("food", "drink"):
            category = "food"
        meal_type = data.get("meal_type")
        if meal_type not in ("breakfast", "lunch", "dinner", "snack", "drink"):
            meal_type = "drink" if category == "drink" else "snack"
        notes   = data.get("notes") or None
        quality = data.get("quality")
        if quality is not None:
            try:
                quality = max(1, min(10, int(quality)))
            except (TypeError, ValueError):
                quality = None
        calories = data.get("calories")
        if calories is not None:
            try:
                calories = max(0, int(calories))
            except (TypeError, ValueError):
                calories = None
        return {"name": name, "category": category, "meal_type": meal_type, "notes": notes, "quality": quality, "calories": calories}
    except Exception:
        # Fallback: store as-is with no LLM enrichment
        return {"name": raw[:120], "category": "food", "meal_type": "snack", "notes": None, "quality": None, "calories": None}


def _entry_dict(e: models.FoodEntry) -> dict:
    return {
        "id":          e.id,
        "raw_input":   e.raw_input,
        "name":        e.name,
        "category":    e.category,
        "meal_type":   e.meal_type,
        "consumed_at": e.consumed_at.isoformat(),
        "notes":       e.notes,
        "quality":     e.quality,
        "calories":    e.calories,
    }


@router.post("/api/food", status_code=201)
def create_food_entry(payload: dict, request: Request, db: Session = Depends(get_db)):
    raw = (payload.get("raw_input") or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="raw_input is required")

    # Default to the client's local time (derived from the UTC offset header),
    # stored as a naive datetime so date-range filtering works correctly.
    # Parse it first so we can pass the local hour to the LLM for meal classification.
    offset_mins = utc_offset_minutes(request)
    local_now = datetime.now(timezone.utc) - timedelta(minutes=offset_mins)
    consumed_at = local_now.replace(tzinfo=None)

    # Allow explicit override (e.g. health page date picker for logging to a past day).
    if payload.get("consumed_at"):
        try:
            consumed_at = datetime.fromisoformat(payload["consumed_at"])
        except ValueError:
            pass

    parsed = _parse_food(raw, hour=consumed_at.hour)

    entry = models.FoodEntry(
        raw_input=raw,
        consumed_at=consumed_at,
        **parsed,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _entry_dict(entry)


@router.get("/api/food")
def get_food_entries(request: Request, date_str: str = None, db: Session = Depends(get_db)):
    """Return entries for a given date (YYYY-MM-DD). Defaults to local today."""
    today = local_date(request)
    target = date_str or today.isoformat()
    try:
        d = date.fromisoformat(target)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")

    next_day = d + timedelta(days=1)
    result = (
        db.query(models.FoodEntry)
        .filter(models.FoodEntry.consumed_at >= d.isoformat(),
                models.FoodEntry.consumed_at <  next_day.isoformat())
        .order_by(models.FoodEntry.consumed_at)
        .all()
    )
    return [_entry_dict(e) for e in result]


@router.get("/api/food/quality-trend")
def get_food_quality_trend(days: int = 30, db: Session = Depends(get_db)):
    """Return daily average food quality score for the last N days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    entries = (
        db.query(models.FoodEntry)
        .filter(
            models.FoodEntry.quality.isnot(None),
            models.FoodEntry.consumed_at >= cutoff,
        )
        .order_by(models.FoodEntry.consumed_at)
        .all()
    )
    by_date: dict[str, list[int]] = {}
    for e in entries:
        d = str(e.consumed_at)[:10]
        by_date.setdefault(d, []).append(e.quality)
    return [
        {"date": d, "value": round(sum(qs) / len(qs), 2)}
        for d, qs in sorted(by_date.items())
    ]


@router.delete("/api/food/{entry_id}")
def delete_food_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(models.FoodEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}
