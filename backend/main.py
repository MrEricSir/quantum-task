import os
import secrets
import requests as http_requests
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import text, or_
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone, date, timedelta

import hashlib
import json
from openai import OpenAI

import icalendar
import models
import schemas
import gcal as gcal_lib
from database import SessionLocal, engine
from model_plugins import get_plugin
from fastapi.responses import Response as FastAPIResponse
from fastapi.staticfiles import StaticFiles

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY  = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL    = os.getenv("LLM_MODEL", os.getenv("OLLAMA_MODEL", "llama3.2"))
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:5173")


def llm_client() -> OpenAI:
    return OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

# ── DB bootstrap ────────────────────────────────────────────────────────────

# Migrate calendar_mappings to current schema.
# v1 had a calendar_id column — drop and recreate.
# v2 had a unique constraint on tag_id — recreate without it and add name column.
with engine.connect() as _conn:
    try:
        _conn.execute(text("SELECT calendar_id FROM calendar_mappings LIMIT 1"))
        _conn.execute(text("DROP TABLE calendar_mappings"))
        _conn.commit()
    except Exception:
        pass  # not on v1 schema

with engine.connect() as _conn:
    table_exists = _conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='calendar_mappings'"
    )).fetchone()
    if table_exists:
        try:
            _conn.execute(text("SELECT name FROM calendar_mappings LIMIT 1"))
        except Exception:
            # name column missing — recreate table without the unique constraint
            _conn.execute(text("""
                CREATE TABLE IF NOT EXISTS calendar_mappings_new (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    ical_url TEXT NOT NULL,
                    name    TEXT NOT NULL DEFAULT ''
                )
            """))
            _conn.execute(text("""
                INSERT INTO calendar_mappings_new (id, tag_id, ical_url, name)
                SELECT id, tag_id, ical_url, '' FROM calendar_mappings
            """))
            _conn.execute(text("DROP TABLE calendar_mappings"))
            _conn.execute(text("ALTER TABLE calendar_mappings_new RENAME TO calendar_mappings"))
            _conn.commit()

models.Base.metadata.create_all(bind=engine)

with engine.connect() as _conn:
    try:
        _conn.execute(text("ALTER TABLE todos ADD COLUMN completed_at DATETIME"))
        _conn.commit()
    except Exception:
        pass  # column already exists
    try:
        _conn.execute(text("ALTER TABLE todos ADD COLUMN raw_input TEXT"))
        _conn.commit()
    except Exception:
        pass  # column already exists
    try:
        _conn.execute(text("ALTER TABLE todos ADD COLUMN recurrence_rule TEXT"))
        _conn.commit()
    except Exception:
        pass  # column already exists

    # habits / habit_completions tables are created by create_all above (new models)
    # No ALTER TABLE needed — they're new tables

    # Migrate briefing_cache to per-section schema (old schema had today_text/week_text columns).
    # Cached briefings are cheap to regenerate, so just drop and recreate.
    try:
        _conn.execute(text("SELECT today_text FROM briefing_cache LIMIT 1"))
        _conn.execute(text("DROP TABLE briefing_cache"))
        _conn.commit()
    except Exception:
        pass  # already on new schema

# Seed default tags
with SessionLocal() as _db:
    for name, color in [("personal", "#8b5cf6"), ("work", "#3b82f6")]:
        if not _db.query(models.Tag).filter_by(name=name).first():
            _db.add(models.Tag(name=name, color=color))
    _db.commit()

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Todo Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Google Calendar ──────────────────────────────────────────────────────────

@app.get("/api/calendar-mappings", response_model=List[schemas.CalendarMappingItem])
def get_calendar_mappings(db: Session = Depends(get_db)):
    return db.query(models.CalendarMapping).all()


@app.put("/api/calendar-mappings")
def set_calendar_mappings(
    mappings: List[schemas.CalendarMappingItem], db: Session = Depends(get_db)
):
    db.query(models.CalendarMapping).delete()
    for m in mappings:
        db.add(models.CalendarMapping(tag_id=m.tag_id, ical_url=m.ical_url))
    db.commit()
    return {"ok": True}


def _get_export_token(db: Session) -> str:
    """Return the current export token, creating one if it doesn't exist."""
    row = db.query(models.AppSetting).filter_by(key="export_token").first()
    if row:
        return row.value
    token = secrets.token_hex(24)
    db.add(models.AppSetting(key="export_token", value=token))
    db.commit()
    return token


@app.get("/api/settings/export-token")
def get_export_token(db: Session = Depends(get_db)):
    return {"token": _get_export_token(db)}


@app.post("/api/settings/export-token/rotate")
def rotate_export_token(db: Session = Depends(get_db)):
    token = secrets.token_hex(24)
    row = db.query(models.AppSetting).filter_by(key="export_token").first()
    if row:
        row.value = token
    else:
        db.add(models.AppSetting(key="export_token", value=token))
    db.commit()
    return {"token": token}


@app.get("/api/calendar-events", response_model=List[schemas.CalendarEvent])
def get_calendar_events(db: Session = Depends(get_db)):
    mappings = db.query(models.CalendarMapping).all()
    if not mappings:
        return []

    today = date.today()
    window_end = today + timedelta(days=28)
    now = datetime.now()

    # uid -> best candidate so far (dict with schema fields + sequence for dedup)
    seen: dict[str, dict] = {}

    for m in mappings:
        tag = db.query(models.Tag).filter(models.Tag.id == m.tag_id).first()
        try:
            for ev in gcal_lib.fetch_events(m.ical_url, today, window_end):
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
                    start_date = start.date() if hasattr(start, "date") else start

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
                    start=ev["start"],
                    end=ev.get("end"),
                    all_day=ev["all_day"],
                    section=section,
                    tag_id=tag.id if tag else None,
                    tag_name=tag.name if tag else None,
                    tag_color=tag.color if tag else None,
                    feed_name=m.name or None,
                )

                uid = ev.get("uid", "")
                if uid:
                    # Deduplicate: keep the version with the highest SEQUENCE number
                    existing = seen.get(uid)
                    if existing is None or candidate["sequence"] >= existing["sequence"]:
                        seen[uid] = candidate
                else:
                    # No UID — use a unique synthetic key so it's never deduped
                    seen[f"{m.id}::{ev['id']}"] = candidate

        except Exception as e:
            print(f"[gcal] failed to fetch events for mapping {m.id}: {e}")

    events = [
        schemas.CalendarEvent(**{k: v for k, v in c.items() if k not in ("uid", "sequence")})
        for c in seen.values()
    ]
    events.sort(key=lambda e: e.start)
    return events


_RRULE_MAP = {
    "daily":   "DAILY",
    "weekly":  "WEEKLY",
    "monthly": "MONTHLY",
    "yearly":  "YEARLY",
}


@app.get("/api/calendar/export.ics")
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
    query = db.query(models.Todo).filter(
        models.Todo.scheduled_at.isnot(None),
        models.Todo.completed == False,  # noqa: E712
    )
    if tag_id is not None:
        query = query.filter(models.Todo.tags.any(models.Tag.id == tag_id))
    todos = query.all()

    tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first() if tag_id else None
    cal_name = f"Quantum Task — {tag.name}" if tag else "Quantum Task"

    cal = icalendar.Calendar()
    cal.add("prodid", "-//Quantum Task//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", cal_name)
    cal.add("x-wr-caldesc", "Tasks exported from Quantum Task")

    for todo in todos:
        ev = icalendar.Event()
        ev.add("uid",     f"todo-{todo.id}@quantumtask")
        ev.add("dtstamp", datetime.now(timezone.utc))
        ev.add("summary", todo.title)

        # scheduled_at may be stored as naive local time — attach UTC for export
        scheduled = todo.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        ev.add("dtstart", scheduled)
        ev.add("dtend",   scheduled + timedelta(hours=1))

        if todo.description:
            ev.add("description", todo.description)
        if todo.recurrence_rule and todo.recurrence_rule in _RRULE_MAP:
            ev.add("rrule", {"FREQ": [_RRULE_MAP[todo.recurrence_rule]]})

        ev.add("status", "CONFIRMED")
        cal.add_component(ev)

    return FastAPIResponse(
        content=cal.to_ical(),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="quantumtask.ics"'},
    )


# ── Briefing ─────────────────────────────────────────────────────────────────

def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


_WMO_EMOJI = {
    0: "☀️",
    1: "🌤️",  2: "⛅",  3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌧️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "🌨️",
    80: "🌦️", 81: "🌧️", 82: "🌧️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}


def _fetch_weather(lat: float, lon: float) -> dict | None:
    try:
        r = http_requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current_weather": "true",
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        cw       = data["current_weather"]
        high     = round(data["daily"]["temperature_2m_max"][0])
        low      = round(data["daily"]["temperature_2m_min"][0])
        emoji    = _WMO_EMOJI.get(int(cw.get("weathercode", 0)), "🌡️")
        if float(cw.get("windspeed", 0)) > 25:
            emoji += "💨"
        return {"emojis": emoji, "high": high, "low": low}
    except Exception:
        return None


def _build_today_context(todos: list, cal_events: list, today: date, habits: list = None) -> str:
    lines = [f"Today is {today.strftime('%A, %B %d, %Y')}."]
    if cal_events:
        lines.append("Calendar events:")
        for e in cal_events:
            if e.all_day:
                lines.append(f"  - {e.title} (all day)")
            else:
                lines.append(f"  - {e.title} at {_fmt_time(e.start)}")
    if todos:
        lines.append("Tasks:")
        for t in todos:
            suffix = f" at {_fmt_time(t.scheduled_at)}" if t.scheduled_at else ""
            overdue = f" [OVERDUE by {t.overdue_days} day{'s' if t.overdue_days != 1 else ''}]" if t.overdue_days > 0 else ""
            lines.append(f"  - {t.title}{suffix}{overdue}")
    if habits:
        pending = [h.name for h in habits if not h.completed_today]
        done = [h.name for h in habits if h.completed_today]
        lines.append("Daily habits:")
        for name in done:
            lines.append(f"  - {name} [done]")
        for name in pending:
            lines.append(f"  - {name} [not yet done]")
    if not cal_events and not todos and not habits:
        lines.append("No tasks or events scheduled.")
    return "\n".join(lines)


def _build_week_context(todos: list, cal_events: list, today: date) -> str | None:
    if not todos and not cal_events:
        return None

    # Bucket all items by calendar date; value is list of (sort_dt, text)
    by_day: dict[date, list[tuple]] = {}
    unscheduled: list[str] = []

    for e in cal_events:
        start = e.start
        day = start.date() if hasattr(start, "date") else start
        if e.all_day:
            by_day.setdefault(day, []).append((None, f"- {e.title} (all day)"))
        else:
            by_day.setdefault(day, []).append((start, f"- {e.title} at {_fmt_time(start)}"))

    for t in todos:
        if t.scheduled_at:
            day = t.scheduled_at.date()
            by_day.setdefault(day, []).append((t.scheduled_at, f"- {t.title} at {_fmt_time(t.scheduled_at)}"))
        else:
            unscheduled.append(f"- {t.title}")

    lines = [f"Today is {today.strftime('%A, %B %d, %Y')}."]

    for day in sorted(by_day):
        lines.append(f"\n{day.strftime('%A, %B %d')}:")
        # All-day items first (sort key None), then timed items chronologically
        items = sorted(by_day[day], key=lambda x: (x[0] is not None, x[0] or datetime.min))
        for _, text in items:
            lines.append(f"  {text}")

    if unscheduled:
        lines.append("\nNo specific day:")
        for item in unscheduled:
            lines.append(f"  {item}")

    return "\n".join(lines)


BRIEFING_MAX_AGE_HOURS = 12


def _today_hash(todos: list, events: list, habits: list, has_location: bool) -> str:
    payload = {
        "todos": [
            {"id": t.id, "title": t.title, "scheduled_at": t.scheduled_at.isoformat() if t.scheduled_at else None}
            for t in sorted(todos, key=lambda t: t.id)
        ],
        "events": [
            {"id": e.id, "title": e.title, "start": e.start.isoformat(), "all_day": e.all_day}
            for e in sorted(events, key=lambda e: e.id)
        ],
        "habits": [
            {"name": h.name, "completed_today": h.completed_today}
            for h in sorted(habits, key=lambda h: h.name)
        ],
        "has_location": has_location,
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _week_hash(todos: list, events: list) -> str:
    payload = {
        "todos": [
            {"id": t.id, "title": t.title, "scheduled_at": t.scheduled_at.isoformat() if t.scheduled_at else None}
            for t in sorted(todos, key=lambda t: t.id)
        ],
        "events": [
            {"id": e.id, "title": e.title, "start": e.start.isoformat(), "all_day": e.all_day}
            for e in sorted(events, key=lambda e: e.id)
        ],
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()


_TODAY_SYSTEM = (
    "Summarise the items listed below. "
    "Write one short sentence per item. "
    "Never combine or connect unrelated items into a single sentence. "
    "Do not add, invent, or infer anything not explicitly listed. "
    "Lead with time-specific events. Be direct. No filler words. "
    "Do not use bullet points, asterisks, dashes, or any markdown formatting. "
    "Output plain prose only."
)
_WEEK_SYSTEM = (
    "Summarise the week ahead using the items listed below, organized by day. "
    "Write one short sentence per day that has items. "
    "Do not combine items from different days. "
    "Do not add, invent, or infer anything not explicitly listed. "
    "Be direct. No filler words. Do not mention weather. "
    "Do not use bullet points, asterisks, dashes, or any markdown formatting. "
    "Output plain prose only."
)
_CACHE_TTL = BRIEFING_MAX_AGE_HOURS * 3600


def _cache_get(section: str, content_hash: str):
    """Return a fresh BriefingCache row for the given section+hash, or None."""
    with SessionLocal() as db:
        row = db.query(models.BriefingCache).filter_by(
            id=f"{section}:{content_hash}"
        ).first()
        if not row:
            return None
        age = (datetime.now(timezone.utc) - row.created_at.replace(tzinfo=timezone.utc)).total_seconds()
        return row if age < _CACHE_TTL else None


def _cache_set(section: str, content_hash: str, text: str, weather_json: str | None = None):
    try:
        with SessionLocal() as db:
            db.merge(models.BriefingCache(
                id=f"{section}:{content_hash}",
                section=section,
                content_hash=content_hash,
                text=text,
                weather_json=weather_json,
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
    except Exception as e:
        print(f"[cache] failed to save {section} briefing: {e}")


@app.post("/api/briefing/stream")
def stream_briefing(req: schemas.BriefingRequest):
    today_dt = date.today()

    today_todos  = [t for t in req.todos if t.section == "today"]
    today_events = [e for e in req.calendar_events if e.section == "today"]
    week_todos   = [t for t in req.todos if t.section == "week"]
    week_events  = [e for e in req.calendar_events if e.section == "week"]

    today_h = _today_hash(today_todos, today_events, req.habits, req.lat is not None)
    week_h  = _week_hash(week_todos, week_events)

    # ── Per-section cache lookup ───────────────────────────────────────────────
    cached_today = cached_weather = cached_week = None
    if not req.force:
        row_t = _cache_get("today", today_h)
        if row_t:
            cached_today   = row_t.text
            cached_weather = row_t.weather_json
        if not req.today_only:
            row_w = _cache_get("week", week_h)
            if row_w:
                cached_week = row_w.text

    need_week = not req.today_only
    all_cached = cached_today is not None and (not need_week or cached_week is not None)

    if all_cached:
        def replay():
            if cached_weather:
                yield f"data: {cached_weather}\n\n"
            yield f"data: {json.dumps({'section': 'today', 'text': cached_today})}\n\n"
            if cached_week:
                yield f"data: {json.dumps({'section': 'week', 'text': cached_week})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(replay(), media_type="text/event-stream",
                                 headers={"X-Briefing-Cached": "true"})

    # ── Generate missing sections ──────────────────────────────────────────────
    weather = _fetch_weather(req.lat, req.lon) if req.lat is not None and req.lon is not None else None
    today_ctx = _build_today_context(today_todos, today_events, today_dt, req.habits)
    week_ctx  = _build_week_context(week_todos, week_events, today_dt) if need_week else None

    def generate():
        weather_raw: str | None = None

        # ── Today section ──────────────────────────────────────────────────────
        if cached_today is not None:
            if cached_weather:
                yield f"data: {cached_weather}\n\n"
            yield f"data: {json.dumps({'section': 'today', 'text': cached_today})}\n\n"
        else:
            if weather:
                weather_raw = json.dumps({'type': 'weather', **weather})
                yield f"data: {weather_raw}\n\n"

            today_acc: list[str] = []
            if not (today_todos or today_events or req.habits):
                text = 'Nothing scheduled today.'
                yield f"data: {json.dumps({'section': 'today', 'text': text})}\n\n"
                today_acc.append(text)
            else:
                try:
                    stream = llm_client().chat.completions.create(
                        model=LLM_MODEL,
                        messages=[{"role": "system", "content": _TODAY_SYSTEM},
                                  {"role": "user",   "content": today_ctx}],
                        stream=True, temperature=0.1,
                    )
                    for chunk in stream:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            yield f"data: {json.dumps({'section': 'today', 'text': delta})}\n\n"
                            today_acc.append(delta)
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

            _cache_set("today", today_h, ''.join(today_acc), weather_raw)

        # ── Week section ───────────────────────────────────────────────────────
        if need_week:
            if cached_week is not None:
                yield f"data: {json.dumps({'section': 'week', 'text': cached_week})}\n\n"
            elif week_ctx:
                week_acc: list[str] = []
                try:
                    stream = llm_client().chat.completions.create(
                        model=LLM_MODEL,
                        messages=[{"role": "system", "content": _WEEK_SYSTEM},
                                  {"role": "user",   "content": week_ctx}],
                        stream=True, temperature=0.1,
                    )
                    for chunk in stream:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            yield f"data: {json.dumps({'section': 'week', 'text': delta})}\n\n"
                            week_acc.append(delta)
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"

                if week_acc:
                    _cache_set("week", week_h, ''.join(week_acc))

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Tags ─────────────────────────────────────────────────────────────────────

@app.get("/api/tags", response_model=List[schemas.Tag])
def get_tags(db: Session = Depends(get_db)):
    return db.query(models.Tag).order_by(models.Tag.name).all()


@app.post("/api/tags", response_model=schemas.Tag, status_code=201)
def create_tag(tag: schemas.TagCreate, db: Session = Depends(get_db)):
    if db.query(models.Tag).filter_by(name=tag.name).first():
        raise HTTPException(status_code=409, detail="Tag already exists")
    db_tag = models.Tag(**tag.model_dump())
    db.add(db_tag)
    db.commit()
    db.refresh(db_tag)
    return db_tag


@app.put("/api/tags/{tag_id}", response_model=schemas.Tag)
def update_tag(tag_id: int, tag: schemas.TagUpdate, db: Session = Depends(get_db)):
    db_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not db_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    if tag.name is not None:
        existing = db.query(models.Tag).filter_by(name=tag.name).first()
        if existing and existing.id != tag_id:
            raise HTTPException(status_code=409, detail="Tag name already exists")
        db_tag.name = tag.name
    if tag.color is not None:
        db_tag.color = tag.color
    db.commit()
    db.refresh(db_tag)
    return db_tag


@app.post("/api/tags/{tag_id}/replace")
def replace_tag(tag_id: int, body: schemas.TagReplacement, db: Session = Depends(get_db)):
    """Move all todos from tag_id to new_tag_id, then delete tag_id."""
    from_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    to_tag = db.query(models.Tag).filter(models.Tag.id == body.new_tag_id).first()
    if not from_tag or not to_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    todos_with_tag = (
        db.query(models.Todo)
        .filter(models.Todo.tags.any(models.Tag.id == tag_id))
        .all()
    )
    for todo in todos_with_tag:
        if from_tag in todo.tags:
            todo.tags.remove(from_tag)
        if to_tag not in todo.tags:
            todo.tags.append(to_tag)
    db.delete(from_tag)
    db.commit()
    return {"ok": True}


@app.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    db_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not db_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.delete(db_tag)
    db.commit()
    return {"ok": True}


# ── Habits ───────────────────────────────────────────────────────────────────

def _compute_streak(db: Session, habit_id: int) -> int:
    today = date.today()
    today_done = db.query(models.HabitCompletion).filter_by(
        habit_id=habit_id, date=today.isoformat()
    ).first() is not None

    streak = 0
    check = today if today_done else today - timedelta(days=1)
    while True:
        done = db.query(models.HabitCompletion).filter_by(
            habit_id=habit_id, date=check.isoformat()
        ).first()
        if done:
            streak += 1
            check -= timedelta(days=1)
        else:
            break
    return streak


def _habit_out(db: Session, habit: models.Habit) -> schemas.Habit:
    today_str = date.today().isoformat()
    completed_today = db.query(models.HabitCompletion).filter_by(
        habit_id=habit.id, date=today_str
    ).first() is not None
    return schemas.Habit(
        id=habit.id,
        name=habit.name,
        created_at=habit.created_at,
        tags=list(habit.tags),
        completed_today=completed_today,
        streak=_compute_streak(db, habit.id),
    )


@app.get("/api/notes", response_model=List[schemas.Note])
def get_notes(db: Session = Depends(get_db)):
    return db.query(models.Note).order_by(models.Note.updated_at.desc()).all()


@app.post("/api/notes", response_model=schemas.Note, status_code=201)
def create_note(note: schemas.NoteCreate, db: Session = Depends(get_db)):
    data = note.model_dump()
    tag_ids = data.pop("tag_ids", [])
    now = datetime.now(timezone.utc)
    db_note = models.Note(**data, created_at=now, updated_at=now)
    if tag_ids:
        db_note.tags = db.query(models.Tag).filter(models.Tag.id.in_(tag_ids)).all()
    db.add(db_note)
    db.commit()
    db.refresh(db_note)
    return db_note


@app.put("/api/notes/{note_id}", response_model=schemas.Note)
def update_note(note_id: int, note: schemas.NoteUpdate, db: Session = Depends(get_db)):
    db_note = db.query(models.Note).filter(models.Note.id == note_id).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    data = note.model_dump(exclude_unset=True)
    tag_ids = data.pop("tag_ids", None)
    for key, val in data.items():
        setattr(db_note, key, val)
    db_note.updated_at = datetime.now(timezone.utc)
    if tag_ids is not None:
        db_note.tags = db.query(models.Tag).filter(models.Tag.id.in_(tag_ids)).all()
    db.commit()
    db.refresh(db_note)
    return db_note


@app.delete("/api/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    db_note = db.query(models.Note).filter(models.Note.id == note_id).first()
    if db_note:
        db.delete(db_note)
        db.commit()


@app.post("/api/notes/{note_id}/promote", response_model=schemas.Todo, status_code=201)
def promote_note(note_id: int, db: Session = Depends(get_db)):
    db_note = db.query(models.Note).filter(models.Note.id == note_id).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    title = db_note.title or (db_note.content.split('\n')[0][:120].strip() if db_note.content else "Untitled")
    count = db.query(models.Todo).filter(models.Todo.section == "later").count()
    db_todo = models.Todo(
        title=title,
        description=db_note.content or None,
        section="later",
        position=count,
        created_at=datetime.now(timezone.utc),
    )
    db_todo.tags = list(db_note.tags)
    db.add(db_todo)
    db.commit()
    db.refresh(db_todo)
    return db_todo


@app.get("/api/habits", response_model=List[schemas.Habit])
def get_habits(db: Session = Depends(get_db)):
    habits = db.query(models.Habit).order_by(models.Habit.created_at).all()
    return [_habit_out(db, h) for h in habits]


@app.post("/api/habits", response_model=schemas.Habit, status_code=201)
def create_habit(habit: schemas.HabitCreate, db: Session = Depends(get_db)):
    db_habit = models.Habit(name=habit.name)
    if habit.tag_ids:
        db_habit.tags = db.query(models.Tag).filter(models.Tag.id.in_(habit.tag_ids)).all()
    db.add(db_habit)
    db.commit()
    db.refresh(db_habit)
    return _habit_out(db, db_habit)


@app.put("/api/habits/{habit_id}", response_model=schemas.Habit)
def update_habit(habit_id: int, habit: schemas.HabitUpdate, db: Session = Depends(get_db)):
    db_habit = db.query(models.Habit).filter(models.Habit.id == habit_id).first()
    if not db_habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    if habit.name is not None:
        db_habit.name = habit.name
    if habit.tag_ids is not None:
        db_habit.tags = db.query(models.Tag).filter(models.Tag.id.in_(habit.tag_ids)).all()
    db.commit()
    db.refresh(db_habit)
    return _habit_out(db, db_habit)


@app.delete("/api/habits/{habit_id}")
def delete_habit(habit_id: int, db: Session = Depends(get_db)):
    db_habit = db.query(models.Habit).filter(models.Habit.id == habit_id).first()
    if not db_habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    db.delete(db_habit)
    db.commit()
    return {"ok": True}


@app.post("/api/habits/{habit_id}/check")
def check_habit(habit_id: int, db: Session = Depends(get_db)):
    today_str = date.today().isoformat()
    if not db.query(models.HabitCompletion).filter_by(habit_id=habit_id, date=today_str).first():
        db.add(models.HabitCompletion(habit_id=habit_id, date=today_str))
        db.commit()
    return {"ok": True}


@app.delete("/api/habits/{habit_id}/check")
def uncheck_habit(habit_id: int, db: Session = Depends(get_db)):
    today_str = date.today().isoformat()
    row = db.query(models.HabitCompletion).filter_by(habit_id=habit_id, date=today_str).first()
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


# ── Todos ─────────────────────────────────────────────────────────────────────

_SECTION_ORDER = {"today": 0, "week": 1, "month": 2, "later": 3}

def _auto_migrate_sections(db: Session) -> None:
    """Advance todos with scheduled_at into the correct section based on today's date.
    Only moves forward (e.g. week → today); never pushes a todo to a later section."""
    today = date.today()
    todos = (
        db.query(models.Todo)
        .filter(models.Todo.completed == False, models.Todo.scheduled_at.isnot(None))
        .all()
    )
    changed = False
    for todo in todos:
        delta = (todo.scheduled_at.date() - today).days
        if delta <= 0:
            target = "today"
        elif delta <= 7:
            target = "week"
        elif delta <= 30:
            target = "month"
        else:
            target = "later"
        if _SECTION_ORDER[target] < _SECTION_ORDER.get(todo.section, 3):
            todo.section = target
            changed = True
    if changed:
        db.commit()


@app.get("/api/todos/search", response_model=List[schemas.Todo])
def search_todos(q: str = Query(default="", min_length=1), db: Session = Depends(get_db)):
    pattern = f"%{q}%"
    return (
        db.query(models.Todo)
        .filter(
            or_(
                models.Todo.title.ilike(pattern),
                models.Todo.description.ilike(pattern),
            )
        )
        .order_by(models.Todo.completed, models.Todo.section, models.Todo.position)
        .limit(30)
        .all()
    )


@app.get("/api/todos", response_model=List[schemas.Todo])
def get_todos(db: Session = Depends(get_db)):
    _auto_migrate_sections(db)
    return (
        db.query(models.Todo)
        .order_by(models.Todo.section, models.Todo.position)
        .all()
    )


@app.post("/api/todos", response_model=schemas.Todo, status_code=201)
def create_todo(todo: schemas.TodoCreate, db: Session = Depends(get_db)):
    count = db.query(models.Todo).filter(models.Todo.section == todo.section).count()
    data = todo.model_dump()
    tag_ids = data.pop("tag_ids", [])
    db_todo = models.Todo(**data, position=count)
    if tag_ids:
        db_todo.tags = db.query(models.Tag).filter(models.Tag.id.in_(tag_ids)).all()
    db.add(db_todo)
    db.commit()
    db.refresh(db_todo)
    return db_todo


@app.post("/api/todos/reorder")
def reorder_todos(updates: List[schemas.TodoReorderItem], db: Session = Depends(get_db)):
    for item in updates:
        db_todo = db.query(models.Todo).filter(models.Todo.id == item.id).first()
        if db_todo:
            db_todo.section = item.section
            db_todo.position = item.position
    db.commit()
    return {"ok": True}


@app.post("/api/todos/parse", response_model=schemas.ParsedTodo)
def parse_todo(req: schemas.ParseRequest, db: Session = Depends(get_db)):
    today = date.today()
    tomorrow = today + timedelta(days=1)
    tag_names = [t.name for t in db.query(models.Tag).order_by(models.Tag.name).all()]
    tags_section = (
        f"Available tags: {', '.join(tag_names)}"
        if tag_names
        else "No tags available."
    )
    plugin = get_plugin(LLM_MODEL)
    prompt = plugin.get_system_prompt(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        tomorrow=tomorrow.isoformat(),
        tags_section=tags_section,
    )
    try:
        client = llm_client()
        response = client.chat.completions.create(
            model=plugin.model_name,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": req.text},
            ],
        )
        raw = plugin.normalize_raw(json.loads(response.choices[0].message.content))
        parsed = plugin.post_process(schemas.ParsedTodo.model_validate(raw), text=req.text)
        return parsed
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"LLM request failed ({LLM_BASE_URL}, model={plugin.model_name}): {e}",
        )


import calendar as _calendar

def _next_occurrence(base: datetime, rule: str) -> datetime:
    rule = rule.lower().strip()
    if rule == "daily":
        return base + timedelta(days=1)
    if rule == "weekly":
        return base + timedelta(weeks=1)
    if rule == "monthly":
        month = base.month % 12 + 1
        year = base.year + (1 if base.month == 12 else 0)
        day = min(base.day, _calendar.monthrange(year, month)[1])
        return base.replace(year=year, month=month, day=day)
    if rule == "yearly":
        return base.replace(year=base.year + 1)
    return base + timedelta(weeks=1)


def _section_for_date(d: date, today: date) -> str:
    delta = (d - today).days
    if delta <= 0:
        return "today"
    if delta <= 7:
        return "week"
    if delta <= 30:
        return "month"
    return "later"


@app.put("/api/todos/{todo_id}", response_model=schemas.Todo)
def update_todo(todo_id: int, todo: schemas.TodoUpdate, db: Session = Depends(get_db)):
    db_todo = db.query(models.Todo).filter(models.Todo.id == todo_id).first()
    if not db_todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    data = todo.model_dump(exclude_unset=True)
    tag_ids = data.pop("tag_ids", None)
    completing = data.get("completed") and not db_todo.completed
    if "completed" in data:
        if completing:
            db_todo.completed_at = datetime.now(timezone.utc)
        elif not data["completed"]:
            db_todo.completed_at = None
    for key, value in data.items():
        setattr(db_todo, key, value)
    if tag_ids is not None:
        db_todo.tags = db.query(models.Tag).filter(models.Tag.id.in_(tag_ids)).all()

    # Spawn next occurrence when completing a recurring todo
    if completing and db_todo.recurrence_rule:
        base = db_todo.scheduled_at or datetime.now(timezone.utc)
        next_dt = _next_occurrence(base, db_todo.recurrence_rule)
        next_section = _section_for_date(next_dt.date(), date.today())
        count = db.query(models.Todo).filter(models.Todo.section == next_section).count()
        next_todo = models.Todo(
            title=db_todo.title,
            description=db_todo.description,
            section=next_section,
            scheduled_at=next_dt,
            recurrence_rule=db_todo.recurrence_rule,
            position=count,
            tags=list(db_todo.tags),
        )
        db.add(next_todo)

    db.commit()
    db.refresh(db_todo)
    return db_todo


@app.post("/api/todos/{todo_id}/tags/{tag_id}")
def add_tag_to_todo(todo_id: int, tag_id: int, db: Session = Depends(get_db)):
    db_todo = db.query(models.Todo).filter(models.Todo.id == todo_id).first()
    db_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not db_todo or not db_tag:
        raise HTTPException(status_code=404, detail="Not found")
    if db_tag not in db_todo.tags:
        db_todo.tags.append(db_tag)
        db.commit()
    return {"ok": True}


@app.delete("/api/todos/{todo_id}/tags/{tag_id}")
def remove_tag_from_todo(todo_id: int, tag_id: int, db: Session = Depends(get_db)):
    db_todo = db.query(models.Todo).filter(models.Todo.id == todo_id).first()
    db_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not db_todo or not db_tag:
        raise HTTPException(status_code=404, detail="Not found")
    if db_tag in db_todo.tags:
        db_todo.tags.remove(db_tag)
        db.commit()
    return {"ok": True}


@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    db_todo = db.query(models.Todo).filter(models.Todo.id == todo_id).first()
    if not db_todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    db.delete(db_todo)
    db.commit()
    return {"ok": True}


# Serve the bundled frontend for all non-API routes.
# Must be mounted last so API routes take precedence.
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
