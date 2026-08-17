"""
Shared scaffolding for "dated log entry" features (food, workouts): parse-and-create
from raw text, list-by-day, delete. Used by routers/food.py and routers/workouts.py.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Callable

from fastapi import Request
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

from deps import local_date, utc_offset_minutes


def resolve_local_timestamp(request: Request, payload: dict, key: str) -> datetime:
    """Client's local time (from the UTC offset header), as a naive datetime so
    date-range filtering works correctly. Overridable via payload[key] (e.g. the
    health page's date picker for logging to a past day) -- an unparsable override
    is silently ignored and the computed default is kept."""
    offset_mins = utc_offset_minutes(request)
    local_now = datetime.now(timezone.utc) - timedelta(minutes=offset_mins)
    timestamp = local_now.replace(tzinfo=None)

    if payload.get(key):
        try:
            timestamp = datetime.fromisoformat(payload[key])
        except ValueError:
            pass

    return timestamp


def day_bounds(request: Request, date_str: str | None) -> tuple[date, date]:
    """(day, day + 1) for a given date_str (YYYY-MM-DD), defaulting to local today."""
    today = local_date(request)
    target = date_str or today.isoformat()
    try:
        d = date.fromisoformat(target)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")
    return d, d + timedelta(days=1)


def create_logged_entries(
    db: Session,
    request: Request,
    payload: dict,
    timestamp_key: str,
    parse_fn: Callable[[str], list[dict]],
    build_row: Callable[[dict, datetime], object],
    serialize: Callable[[object], dict],
) -> list[dict]:
    """Shared create flow: validate raw_input, resolve the entry timestamp, parse
    the raw text into one or more items, build+add a row per item, commit once."""
    raw = (payload.get("raw_input") or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="raw_input is required")

    timestamp = resolve_local_timestamp(request, payload, timestamp_key)
    parsed_items = parse_fn(raw)

    rows = [build_row(item, timestamp) for item in parsed_items]
    for row in rows:
        db.add(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return [serialize(row) for row in rows]


def delete_logged_entry(db: Session, model_cls, entry_id: int) -> None:
    entry = db.query(model_cls).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
