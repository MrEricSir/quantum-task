import secrets
from datetime import date as date_type, datetime, timedelta, timezone
from typing import List

import icalendar
from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import Response as FastAPIResponse
from sqlalchemy.orm import Session

import gcal as gcal_lib
from gcal import _cached_fetch_events
import models
import schemas
import app_setting_keys as setting_keys
from deps import get_db, local_date, utc_offset_minutes as get_utc_offset

router = APIRouter()

_RRULE_MAP = {
    "daily":   "DAILY",
    "weekly":  "WEEKLY",
    "monthly": "MONTHLY",
    "yearly":  "YEARLY",
}


def _get_export_token(db: Session) -> str:
    """Return the current export token, creating one if it doesn't exist."""
    row = db.query(models.AppSetting).filter_by(key=setting_keys.EXPORT_TOKEN).first()
    if row:
        return row.value
    token = secrets.token_hex(24)
    db.add(models.AppSetting(key=setting_keys.EXPORT_TOKEN, value=token))
    db.commit()
    return token


@router.get("/api/calendar-mappings", response_model=List[schemas.CalendarMappingItem])
def get_calendar_mappings(db: Session = Depends(get_db)):
    return db.query(models.CalendarMapping).all()


@router.put("/api/calendar-mappings")
def set_calendar_mappings(
    mappings: List[schemas.CalendarMappingItem], db: Session = Depends(get_db)
):
    db.query(models.CalendarMapping).delete()
    for m in mappings:
        db.add(models.CalendarMapping(tag_id=m.tag_id, ical_url=m.ical_url))
    db.commit()
    return {"ok": True}


@router.get("/api/settings/export-token")
def get_export_token(db: Session = Depends(get_db)):
    return {"token": _get_export_token(db)}


@router.post("/api/settings/export-token/rotate")
def rotate_export_token(db: Session = Depends(get_db)):
    token = secrets.token_hex(24)
    row = db.query(models.AppSetting).filter_by(key=setting_keys.EXPORT_TOKEN).first()
    if row:
        row.value = token
    else:
        db.add(models.AppSetting(key=setting_keys.EXPORT_TOKEN, value=token))
    db.commit()
    return {"token": token}


@router.get("/api/calendar-events", response_model=List[schemas.CalendarEvent])
def get_calendar_events(request: Request, db: Session = Depends(get_db), force: bool = Query(False)):
    mappings = db.query(models.CalendarMapping).all()
    if not mappings:
        return []

    today = local_date(request)
    offset_minutes = get_utc_offset(request)
    window_end = today + timedelta(days=28)
    now = datetime.now(timezone.utc)

    def _to_local_date(dt) -> date_type:
        """Convert a UTC-aware datetime to the client's local date."""
        if isinstance(dt, datetime) and dt.tzinfo is not None:
            local_dt = dt.replace(tzinfo=None) - timedelta(minutes=offset_minutes)
            return local_dt.date()
        return dt.date() if hasattr(dt, "date") else dt

    # uid -> best candidate so far (dict with schema fields + sequence for dedup)
    seen: dict[str, dict] = {}

    for m in mappings:
        tag = db.query(models.Tag).filter(models.Tag.id == m.tag_id).first()
        try:
            for ev in _cached_fetch_events(m.ical_url, today, window_end, force=force):
                start = ev["start"]
                end = ev.get("end")

                if ev["all_day"]:
                    start_date = start.date() if hasattr(start, "date") else start
                    if (start_date - today).days < 0:
                        continue
                else:
                    cutoff = end if end else start
                    if cutoff < now:
                        continue
                    start_date = _to_local_date(start)

                delta = (start_date - today).days
                if delta == 0:
                    section = "today"
                elif delta <= 7:
                    section = "week"
                else:
                    section = "month"

                candidate = dict(
                    id=f"{m.id}::{ev['id']}",
                    uid=ev.get("uid", ""),
                    sequence=ev.get("sequence", 0),
                    title=ev["title"],
                    description=ev.get("description"),
                    location=ev.get("location"),
                    url=ev.get("url"),
                    start=ev["start"],
                    end=ev.get("end"),
                    all_day=ev["all_day"],
                    section=section,
                    tag_id=tag.id if tag else None,
                    tag_name=tag.name if tag else None,
                    tag_color=tag.color if tag else None,
                    feed_name=m.name or None,
                    is_ooo=ev.get("is_ooo", False),
                )

                uid = ev.get("uid", "")
                if uid:
                    # Key by uid + start time: deduplicates same occurrence across feeds
                    # while preserving distinct recurring event instances.
                    dedup_key = f"{uid}::{candidate['start'].isoformat()}"
                    existing = seen.get(dedup_key)
                    if existing is None or candidate["sequence"] >= existing["sequence"]:
                        seen[dedup_key] = candidate
                else:
                    seen[f"{m.id}::{ev['id']}"] = candidate

        except Exception as e:
            print(f"[gcal] failed to fetch events for mapping {m.id}: {e}")

    events = [
        schemas.CalendarEvent(**{k: v for k, v in c.items() if k not in ("uid", "sequence")})
        for c in seen.values()
    ]
    events.sort(key=lambda e: e.start if e.start.tzinfo is not None else e.start.replace(tzinfo=timezone.utc))
    return events


@router.get("/api/calendar/export.ics")
def export_calendar_ical(
    token: str = Query(...),
    tag_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Export scheduled tasks as a subscribable iCal feed.

    Requires ?token=<export_token>. Optional ?tag_id=N filters to a single tag.
    """
    valid_token = _get_export_token(db)
    if not secrets.compare_digest(token, valid_token):
        raise HTTPException(status_code=401, detail="Invalid export token")
    query = db.query(models.Card).filter(
        models.Card.scheduled_at.isnot(None),
        models.Card.completed == False,  # noqa: E712
    )
    if tag_id is not None:
        query = query.filter(models.Card.tags.any(models.Tag.id == tag_id))
    cards = query.all()

    tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first() if tag_id else None
    cal_name = f"Quantum Task — {tag.name}" if tag else "Quantum Task"

    cal = icalendar.Calendar()
    cal.add("prodid", "-//Quantum Task//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", cal_name)
    cal.add("x-wr-caldesc", "Tasks exported from Quantum Task")

    for card in cards:
        ev = icalendar.Event()
        ev.add("uid",     f"todo-{card.id}@quantumtask")
        ev.add("dtstamp", datetime.now(timezone.utc))
        ev.add("summary", card.title)

        scheduled = card.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        ev.add("dtstart", scheduled)
        ev.add("dtend",   scheduled + timedelta(hours=1))

        desc = card.body or card.description
        if desc:
            ev.add("description", desc)
        if card.recurrence_rule and card.recurrence_rule in _RRULE_MAP:
            ev.add("rrule", {"FREQ": [_RRULE_MAP[card.recurrence_rule]]})

        ev.add("status", "CONFIRMED")
        cal.add_component(ev)

    return FastAPIResponse(
        content=cal.to_ical(),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="quantumtask.ics"'},
    )
