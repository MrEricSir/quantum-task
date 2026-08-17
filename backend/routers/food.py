"""
Food and drink log.

POST   /api/food            – parse raw text with LLM, store one entry per distinct item
GET    /api/food?date=...   – entries for a date (YYYY-MM-DD; defaults to today)
DELETE /api/food/{id}       – remove an entry
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
from capabilities.food import parse_food_entries
from daily_log import create_logged_entries, day_bounds, delete_logged_entry
from deps import get_db, local_date

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


def _build_row(parsed: dict, raw: str, consumed_at: datetime) -> models.FoodEntry:
    return models.FoodEntry(
        raw_input=parsed.pop("source_text", None) or raw,
        consumed_at=consumed_at,
        **parsed,
    )


@router.post("/api/food", status_code=201)
def create_food_entry(payload: dict, request: Request, db: Session = Depends(get_db)):
    raw = (payload.get("raw_input") or "").strip()
    return create_logged_entries(
        db, request, payload, "consumed_at",
        parse_fn=parse_food_entries,
        build_row=lambda parsed, ts: _build_row(parsed, raw, ts),
        serialize=_entry_dict,
    )


@router.get("/api/food")
def get_food_entries(request: Request, date_str: str = None, db: Session = Depends(get_db)):
    """Return entries for a given date (YYYY-MM-DD). Defaults to local today."""
    d, next_day = day_bounds(request, date_str)
    result = (
        db.query(models.FoodEntry)
        .filter(models.FoodEntry.consumed_at >= d.isoformat(),
                models.FoodEntry.consumed_at <  next_day.isoformat())
        .order_by(models.FoodEntry.consumed_at)
        .all()
    )
    return [_entry_dict(e) for e in result]


@router.get("/api/food/quality-trend")
def get_food_quality_trend(request: Request, days: int = 30, db: Session = Depends(get_db)):
    """Return daily average food quality score for the last N days."""
    cutoff = (local_date(request) - timedelta(days=days)).isoformat()
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
    delete_logged_entry(db, models.FoodEntry, entry_id)
    return {"ok": True}
