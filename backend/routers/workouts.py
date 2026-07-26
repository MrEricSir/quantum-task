"""
Workout log.

POST   /api/workouts            – parse raw text with LLM, store entry
GET    /api/workouts?date=...   – entries for a date (YYYY-MM-DD; defaults to today)
DELETE /api/workouts/{id}       – remove an entry
"""

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
from deps import get_db, llm_client, LLM_MODEL, local_date, utc_offset_minutes

router = APIRouter()

WORKOUT_TYPES = {"run", "cycle", "row", "swim", "strength", "yoga", "sport", "other"}

_PARSE_SYSTEM = """\
You parse workout log entries into structured data.
Respond with ONLY valid JSON (no markdown, no explanation).

{
  "type":  "run" | "cycle" | "row" | "swim" | "strength" | "yoga" | "sport" | "other",
  "value": numeric measurement or null (e.g. 5000 for "rowed 5000m", 185 for "bench 185 lbs", 3.1 for "ran 3.1 miles"),
  "unit":  short unit string or null (e.g. "m", "km", "miles", "lbs", "kg", "min"),
  "notes": "one brief sentence describing the workout, or null"
}

Type selection rules:
- run: running, jogging, treadmill
- cycle: biking, cycling, spinning, Peloton
- row: rowing machine, erg, on-water rowing
- swim: swimming, pool laps
- strength: weight lifting, resistance training, gym, bench, squat, deadlift, dumbbells
- yoga: yoga, stretching, pilates, flexibility
- sport: basketball, tennis, soccer, golf, climbing, or any named sport
- other: anything that doesn't fit above

value/unit rules:
- Extract the primary measurement if present (distance, weight, reps, time)
- For strength, prefer the weight used (e.g. "bench pressed 185 lbs" → value=185, unit="lbs")
- For rowing/running/cycling, prefer distance (e.g. "rowed 5k" → value=5, unit="km")
- For yoga/sport/other with only time given, use minutes
- If no measurement is present, set both to null
- IMPORTANT: Never convert units. Preserve the exact value and unit the user provided.
  If the user says "1 mi", output value=1, unit="mi" — do not convert to meters or any other unit.
"""


def _parse_workout(raw: str) -> dict:
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _PARSE_SYSTEM},
                {"role": "user",   "content": raw},
            ],
            max_tokens=150,
        )
        data = json.loads(resp.choices[0].message.content.strip())
        wtype = data.get("type", "other")
        if wtype not in WORKOUT_TYPES:
            wtype = "other"
        value = data.get("value")
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        unit = data.get("unit") or None
        if unit:
            unit = str(unit)[:20]
        notes = data.get("notes") or None
        return {"type": wtype, "value": value, "unit": unit, "notes": notes}
    except Exception:
        return {"type": "other", "value": None, "unit": None, "notes": None}


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


@router.post("/api/workouts", status_code=201)
def create_workout_entry(payload: dict, request: Request, db: Session = Depends(get_db)):
    raw = (payload.get("raw_input") or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="raw_input is required")

    # Default to the client's local time (derived from the UTC offset header),
    # stored as a naive datetime so date-range filtering works correctly.
    offset_mins = utc_offset_minutes(request)
    local_now = datetime.now(timezone.utc) - timedelta(minutes=offset_mins)
    logged_at = local_now.replace(tzinfo=None)

    # Allow explicit override (e.g. health page date picker for logging to a past day).
    if payload.get("logged_at"):
        try:
            logged_at = datetime.fromisoformat(payload["logged_at"])
        except ValueError:
            pass

    parsed = _parse_workout(raw)
    entry = models.WorkoutEntry(raw_input=raw, logged_at=logged_at, **parsed)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _entry_dict(entry)


@router.get("/api/workouts")
def get_workout_entries(request: Request, date_str: str = None, db: Session = Depends(get_db)):
    """Return entries for a given date (YYYY-MM-DD). Defaults to local today."""
    today = local_date(request)
    target = date_str or today.isoformat()
    try:
        d = date.fromisoformat(target)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")

    next_day = d + timedelta(days=1)
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


@router.delete("/api/workouts/{entry_id}")
def delete_workout_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(models.WorkoutEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}
