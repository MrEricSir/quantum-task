"""
Food and drink log.

POST   /api/food            – parse raw text with LLM, store one entry per distinct item
GET    /api/food?date=...   – entries for a date (YYYY-MM-DD; defaults to today)
DELETE /api/food/{id}       – remove an entry
"""

from datetime import date, datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
from capabilities.food import parse_food_entries
from deps import get_db, local_date, utc_offset_minutes

router = APIRouter()


def _entry_dict(e: models.FoodEntry) -> dict:
    return {
        "id":          e.id,
        "raw_input":   e.raw_input,
        "name":        e.name,
        "category":    e.category,
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
    offset_mins = utc_offset_minutes(request)
    local_now = datetime.now(timezone.utc) - timedelta(minutes=offset_mins)
    consumed_at = local_now.replace(tzinfo=None)

    # Allow explicit override (e.g. health page date picker for logging to a past day).
    if payload.get("consumed_at"):
        try:
            consumed_at = datetime.fromisoformat(payload["consumed_at"])
        except ValueError:
            pass

    parsed_items = parse_food_entries(raw)

    entries = []
    for parsed in parsed_items:
        entry = models.FoodEntry(
            raw_input=parsed.pop("source_text", None) or raw,
            consumed_at=consumed_at,
            **parsed,
        )
        db.add(entry)
        entries.append(entry)
    db.commit()
    for entry in entries:
        db.refresh(entry)
    return [_entry_dict(e) for e in entries]


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


@router.put("/api/food/{entry_id}")
def update_food_entry(entry_id: int, payload: dict, db: Session = Depends(get_db)):
    """Manual correction of a logged entry -- no LLM re-parse, just direct
    field edits (the LLM already had its shot when the entry was created)."""
    entry = db.query(models.FoodEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if "name" in payload:
        name = (payload["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="name cannot be empty")
        entry.name = name
    if "category" in payload and payload["category"] in ("food", "drink"):
        entry.category = payload["category"]
    if "notes" in payload:
        entry.notes = payload["notes"] or None
    if "quality" in payload:
        quality = payload["quality"]
        entry.quality = max(1, min(10, int(quality))) if quality is not None else None
    if "calories" in payload:
        calories = payload["calories"]
        entry.calories = max(0, int(calories)) if calories is not None else None
    if "consumed_at" in payload and payload["consumed_at"]:
        try:
            entry.consumed_at = datetime.fromisoformat(payload["consumed_at"])
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid consumed_at format")

    db.commit()
    db.refresh(entry)
    return _entry_dict(entry)


@router.delete("/api/food/{entry_id}")
def delete_food_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(models.FoodEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}
