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
from deps import get_db, llm_client, LLM_MODEL, local_date

router = APIRouter()

_PARSE_SYSTEM = """\
You parse food and drink log entries into structured data. \
Respond with ONLY valid JSON (no markdown, no explanation).

{{
  "name":      "concise name of what was consumed, e.g. 'donut', 'coffee with oat milk', 'chicken salad'",
  "category":  "food" | "drink",
  "meal_type": "breakfast" | "lunch" | "dinner" | "snack" | "drink",
  "notes":     "1-2 sentence honest nutritional assessment — be specific, not preachy",
  "quality":   integer 1-10 (10 = highly nutritious whole foods; 1 = pure junk with no redeeming value)
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

quality examples:
- leafy salad, grilled salmon → 9
- oatmeal with fruit → 8
- banana → 7
- coffee with milk → 6
- white rice with vegetables → 6
- pizza slice → 4
- donut → 3
- bag of chips → 3
- energy drink → 2

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
        return {"name": name, "category": category, "meal_type": meal_type, "notes": notes, "quality": quality}
    except Exception:
        # Fallback: store as-is with no LLM enrichment
        return {"name": raw[:120], "category": "food", "meal_type": "snack", "notes": None, "quality": None}


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
    }


@router.post("/api/food", status_code=201)
def create_food_entry(payload: dict, request: Request, db: Session = Depends(get_db)):
    raw = (payload.get("raw_input") or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="raw_input is required")

    # Allow caller to override consumed_at (e.g. quick add knows the local time).
    # Parse it first so we can pass the local hour to the LLM for meal classification.
    consumed_at = datetime.now(timezone.utc)
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


@router.delete("/api/food/{entry_id}")
def delete_food_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(models.FoodEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}
