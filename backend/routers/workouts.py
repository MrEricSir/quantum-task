"""
Workout log.

POST   /api/workouts            – parse raw text with LLM, store entry
GET    /api/workouts?date=...   – entries for a date (YYYY-MM-DD; defaults to today)
PUT    /api/workouts/{id}       – manual correction of a logged entry
DELETE /api/workouts/{id}       – remove an entry
"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
from capabilities.workout import parse_workout, WORKOUT_TYPES
from daily_log import create_logged_entries, day_bounds, delete_logged_entry
from deps import get_db, local_date

router = APIRouter()


def _entry_dict(e: models.WorkoutEntry) -> dict:
    return {
        "id":        e.id,
        "raw_input": e.raw_input,
        "type":      e.type,
        "value":     e.value,
        "unit":      e.unit,
        "notes":     e.notes,
        "logged_at": e.logged_at.isoformat(),
    }


def _build_row(parsed: dict, raw: str, logged_at: datetime) -> models.WorkoutEntry:
    return models.WorkoutEntry(raw_input=raw, logged_at=logged_at, **parsed)


@router.post("/api/workouts", status_code=201)
def create_workout_entry(payload: dict, request: Request, db: Session = Depends(get_db)):
    raw = (payload.get("raw_input") or "").strip()
    return create_logged_entries(
        db, request, payload, "logged_at",
        parse_fn=lambda r: [parse_workout(r)],
        build_row=lambda parsed, ts: _build_row(parsed, raw, ts),
        serialize=_entry_dict,
    )


@router.get("/api/workouts")
def get_workout_entries(request: Request, date_str: str = None, db: Session = Depends(get_db)):
    """Return entries for a given date (YYYY-MM-DD). Defaults to local today."""
    d, next_day = day_bounds(request, date_str)
    result = (
        db.query(models.WorkoutEntry)
        .filter(
            models.WorkoutEntry.logged_at >= d.isoformat(),
            models.WorkoutEntry.logged_at <  next_day.isoformat(),
        )
        .order_by(models.WorkoutEntry.logged_at)
        .all()
    )
    return [_entry_dict(e) for e in result]


@router.get("/api/workouts/chart")
def get_workout_chart(request: Request, start: str = None, end: str = None, db: Session = Depends(get_db)):
    """Return [{date, types:[]}] for a date range. Defaults to past 30 days."""
    today = local_date(request)
    try:
        end_date   = date.fromisoformat(end)   if end   else today
        start_date = date.fromisoformat(start) if start else (today - timedelta(days=29))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")

    next_day = end_date + timedelta(days=1)
    entries = (
        db.query(models.WorkoutEntry)
        .filter(
            models.WorkoutEntry.logged_at >= start_date.isoformat(),
            models.WorkoutEntry.logged_at <  next_day.isoformat(),
        )
        .order_by(models.WorkoutEntry.logged_at)
        .all()
    )

    # Group by date
    by_date: dict[str, set] = {}
    for e in entries:
        d_str = str(e.logged_at)[:10]
        by_date.setdefault(d_str, set()).add(e.type)

    # Build a complete list for every day in the range (including empty days)
    result = []
    current = start_date
    while current <= end_date:
        d_str = current.isoformat()
        result.append({"date": d_str, "types": sorted(by_date.get(d_str, set()))})
        current += timedelta(days=1)
    return result


@router.put("/api/workouts/{entry_id}")
def update_workout_entry(entry_id: int, payload: dict, db: Session = Depends(get_db)):
    """Manual correction of a logged entry -- no LLM re-parse, just direct
    field edits (the LLM already had its shot when the entry was created)."""
    entry = db.query(models.WorkoutEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if "type" in payload and payload["type"] in WORKOUT_TYPES:
        entry.type = payload["type"]
    if "value" in payload:
        value = payload["value"]
        if value is None:
            entry.value = None
        else:
            try:
                entry.value = float(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="Invalid value")
    if "unit" in payload:
        unit = (payload["unit"] or "").strip()
        entry.unit = unit[:20] or None
    if "notes" in payload:
        entry.notes = payload["notes"] or None
    if "logged_at" in payload and payload["logged_at"]:
        try:
            entry.logged_at = datetime.fromisoformat(payload["logged_at"])
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid logged_at format")

    db.commit()
    db.refresh(entry)
    return _entry_dict(entry)


@router.delete("/api/workouts/{entry_id}")
def delete_workout_entry(entry_id: int, db: Session = Depends(get_db)):
    delete_logged_entry(db, models.WorkoutEntry, entry_id)
    return {"ok": True}
