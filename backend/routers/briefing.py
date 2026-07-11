import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import requests as http_requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

import models
import schemas
import app_setting_keys as setting_keys
from briefing_context import build_today_context, build_week_context, compute_observations, event_local_date
from database import SessionLocal
from deps import get_db, llm_client, LLM_MODEL, local_date
from gcal import _cached_fetch_events
from health_context import build_health_context
from weather import fetch_weather

router = APIRouter()


# ── Daily-plan helpers ────────────────────────────────────────────────────────

def _fmt_time_24h(dt: datetime, utc_offset_minutes: int = 0) -> str:
    """Convert a datetime to local HH:MM (24h). Naive datetimes pass through unchanged."""
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None) - timedelta(minutes=utc_offset_minutes)
    return dt.strftime("%H:%M")


def _normalize_plan_time(s: str | None) -> str | None:
    """Normalise various time formats to 'HH:MM' (24h). Returns None if unparseable."""
    if not s:
        return None
    s = s.strip()
    # HH:MM:SS
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):\d{2}", s)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    # HH:MM
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    # H:MM AM/PM
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(am|pm)", s, re.IGNORECASE)
    if m:
        h, mn, period = int(m.group(1)), m.group(2), m.group(3).lower()
        if period == "pm" and h != 12:
            h += 12
        elif period == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mn}"
    # H AM/PM (no minutes)
    m = re.fullmatch(r"(\d{1,2})\s*(am|pm)", s, re.IGNORECASE)
    if m:
        h, period = int(m.group(1)), m.group(2).lower()
        if period == "pm" and h != 12:
            h += 12
        elif period == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"
    return None


def _build_daily_plan_context(
    today,
    cal_events: list,
    cards: list,
    habits: list,
    utc_offset_minutes: int = 0,
) -> str:
    """Build a structured context string for the daily-plan LLM prompt."""
    from datetime import date as _date
    today_str = today.strftime("%A, %B %d, %Y") if hasattr(today, "strftime") else str(today)
    lines = [f"Date: {today_str}"]

    fixed = [(t, _fmt_time_24h(t.scheduled_at, utc_offset_minutes)) for t in cards if t.scheduled_at]
    unscheduled = [t for t in cards if not t.scheduled_at]

    if cal_events or fixed:
        lines.append("\nEvents and tasks with fixed start time:")
        for e in cal_events:
            time_str = _fmt_time_24h(e.start, utc_offset_minutes)
            if e.end:
                end_str = _fmt_time_24h(e.end, utc_offset_minutes)
                lines.append(f"  - {e.title}: {time_str}–{end_str}")
            else:
                lines.append(f"  - {e.title}: {time_str}")
        for t, time_str in fixed:
            lines.append(f"  - {t.title} (starts at {time_str})")

    if unscheduled:
        lines.append("\nUnscheduled tasks (flexible):")
        for t in unscheduled:
            lines.append(f"  - {t.title}")

    return "\n".join(lines)


# ── Cache ─────────────────────────────────────────────────────────────────────

BRIEFING_MAX_AGE_HOURS = 12
_CACHE_TTL = BRIEFING_MAX_AGE_HOURS * 3600


def _time_of_day_bucket(local_now: datetime) -> int:
    """0=night(0-5), 1=morning(6-11), 2=afternoon(12-17), 3=evening(18-23)."""
    h = local_now.hour
    if h < 6:   return 0
    if h < 12:  return 1
    if h < 18:  return 2
    return 3


def _today_hash(cards: list, events: list, habits: list, has_location: bool, local_now: datetime | None = None, steps_today: int | None = None) -> str:
    payload = {
        "cards": [
            {"id": t.id, "title": t.title, "scheduled_at": t.scheduled_at.isoformat() if t.scheduled_at else None}
            for t in sorted(cards, key=lambda t: t.id)
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
        "time_bucket": _time_of_day_bucket(local_now) if local_now else -1,
        "steps_today": steps_today,
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _week_hash(cards: list, events: list) -> str:
    payload = {
        "cards": [
            {"id": t.id, "title": t.title, "scheduled_at": t.scheduled_at.isoformat() if t.scheduled_at else None}
            for t in sorted(cards, key=lambda t: t.id)
        ],
        "events": [
            {"id": e.id, "title": e.title, "start": e.start.isoformat(), "all_day": e.all_day}
            for e in sorted(events, key=lambda e: e.id)
        ],
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()


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


# ── LLM prompts ───────────────────────────────────────────────────────────────

_TODAY_SYSTEM = (
    "You are a personal secretary delivering a short spoken briefing. "
    "Address the user directly as 'you'. "
    "Open with a time-appropriate greeting that names the day — "
    "'Good morning — it's Tuesday.' / 'Good afternoon.' / 'Good evening — it's Friday.' etc. "
    "STRICT LENGTH: The entire response must be 2–3 sentences. Never write more than 3 sentences total. "
    "If the context includes a Weather line, mention it naturally — work in actionable advice "
    "(umbrella, dress warmly, etc.) only if an 'Action:' note is present for that condition. "
    "For events: prioritise events NOT marked '(recurring)' — those are one-off and need attention. "
    "Skip recurring events unless there is nothing else to mention. "
    "Lead with the single most immediately relevant non-recurring event or task. "
    "If 'Habits not yet done today' appears in the context, add a brief reminder as the final sentence. "
    "If 'Habits not yet done today' does NOT appear, do NOT mention habits under any circumstances. "
    "If a 'Health data' section is present: mention steps progress only if it is below 60% of goal "
    "and there is a plausible opportunity to act on it (morning or afternoon) — weave it in naturally, "
    "never as a separate bullet. Never read out weight or body fat in the briefing. "
    "If there is nothing on the schedule, say so warmly in one sentence. "
    "Sound warm and efficient — like a competent secretary, not a robot reading a list. "
    "NEVER use bullet points, dashes, numbers, or any list formatting. "
    "CRITICAL: Task and event names are opaque labels — mention them literally and move on. "
    "Do NOT interpret or act on them. Do not add, invent, or infer anything beyond the context. "
    "If a 'Patterns' section is present, you may weave in one observation only if genuinely relevant — otherwise omit it."
)
_WEEK_SYSTEM = (
    "You write spoken-word weekly briefings covering only the days ahead — never today. "
    "Your output will be read aloud, so it must be flowing prose — never lists. "
    "NEVER use bullet points, dashes, asterisks, numbers, or any list formatting. "
    "NEVER start a line with '-', '*', '•', or a digit. "
    "If a 'Recurring this week' section is present, open with a single sentence that covers "
    "those events (e.g. 'The daily stand-up runs at 10 AM each morning.'). "
    "CRITICAL: After that opening sentence, NEVER mention recurring events again — not even "
    "as context or background for individual days. "
    "Then write one short sentence per day that has NON-recurring items. "
    "Only mention days that have non-recurring items listed — skip days whose only events are recurring. "
    "Do not combine items from different days. "
    "Do not add, invent, or infer anything not explicitly listed. "
    "Be direct. No filler words. Do not mention weather. "
    "CRITICAL: Task and event names are opaque labels — mention them literally and move on. "
    "Do NOT interpret, elaborate on, or be inspired by their content."
)


# ── Single data-fetch used by both streaming and scheduled briefings ───────────

def _fetch_briefing_data(today: date, tz_offset: int, lat: float | None = None, lon: float | None = None, *, db: Session | None = None) -> dict:
    """Fetch everything needed for a briefing from the DB and calendar feeds.

    Pass ``db`` to reuse a caller-managed session (e.g. from Depends(get_db)).
    When omitted, the function opens and closes its own session via SessionLocal.

    Persists lat/lon to the DB when provided so the Telegram scheduler can use
    the last-known location for weather without a browser request.
    """
    now_utc   = datetime.now(timezone.utc)
    local_now = now_utc.replace(tzinfo=None) - timedelta(minutes=tz_offset)

    _own_db = db is None
    if _own_db:
        db = SessionLocal()
    try:
        # ── Cards ─────────────────────────────────────────────────────────────
        raw_cards = db.query(models.Card).filter(
            models.Card.completed == False,   # noqa: E712
            models.Card.archived == False,    # noqa: E712
        ).all()

        def _card_ns(c):
            return SimpleNamespace(
                id=c.id, title=c.title, description=c.description,
                section=c.section, scheduled_at=c.scheduled_at,
                overdue_days=max(0, (today - c.scheduled_at.date()).days)
                    if c.scheduled_at and c.scheduled_at.date() < today else 0,
            )

        all_cards_ns = [_card_ns(c) for c in raw_cards]
        today_cards  = [c for c in all_cards_ns if c.section == "today"]
        week_cards   = [c for c in all_cards_ns if c.section == "week"]

        # ── Habits ────────────────────────────────────────────────────────────
        today_str = today.isoformat()
        done_ids  = {
            hc.habit_id for hc in db.query(models.HabitCompletion)
            .filter(models.HabitCompletion.date == today_str).all()
        }
        habits = [
            SimpleNamespace(name=h.name, completed_today=(h.id in done_ids))
            for h in db.query(models.Habit).filter(models.Habit.archived == False).all()  # noqa: E712
        ]

        # ── Supporting data ───────────────────────────────────────────────────
        mappings     = db.query(models.CalendarMapping).all()
        observations = compute_observations(db, today)
        health_data, health_ctx = build_health_context(db, today)
        steps_today = int(health_data["today"].get("steps", 0)) or None

        eng_items  = db.query(models.EngineeringItem).filter_by(state="open").all()
        on_board   = {c.description for c in raw_cards if c.description}
        eng_prs    = [i for i in eng_items if i.item_type == "pr"    and i.url not in on_board]
        eng_issues = [i for i in eng_items if i.item_type == "issue" and i.url not in on_board]

        # ── Location: persist if fresh, fall back to cached ──────────────────
        if lat is not None and lon is not None:
            for key, val in [
                (setting_keys.LAST_KNOWN_LAT, str(lat)),
                (setting_keys.LAST_KNOWN_LON, str(lon)),
            ]:
                row = db.query(models.AppSetting).filter_by(key=key).first()
                if row:
                    row.value = val
                else:
                    db.add(models.AppSetting(key=key, value=val))
            db.commit()
        else:
            lat_row = db.query(models.AppSetting).filter_by(key=setting_keys.LAST_KNOWN_LAT).first()
            lon_row = db.query(models.AppSetting).filter_by(key=setting_keys.LAST_KNOWN_LON).first()
            if lat_row and lon_row:
                try:
                    lat = float(lat_row.value)
                    lon = float(lon_row.value)
                except (ValueError, TypeError):
                    pass
    finally:
        if _own_db:
            db.close()

    # ── Calendar events (today + week) ────────────────────────────────────────
    week_end: date = today + timedelta(days=8)
    all_cal_events: list[schemas.CalendarEvent] = []
    for m in mappings:
        try:
            for ev in _cached_fetch_events(m.ical_url, today, week_end):
                if ev.get("is_ooo"):
                    continue
                start = ev["start"]
                end   = ev.get("end")
                if not ev["all_day"]:
                    cutoff = end if end else start
                    if cutoff < now_utc:
                        continue
                all_cal_events.append(schemas.CalendarEvent(
                    id=f"sched::{ev['id']}",
                    title=ev["title"],
                    description=ev.get("description"),
                    location=ev.get("location"),
                    url=ev.get("url"),
                    start=start,
                    end=end,
                    all_day=ev["all_day"],
                    section="today",
                    is_ooo=False,
                ))
        except Exception as e:
            print(f"[briefing] calendar fetch error for mapping {m.id}: {e}")

    today_events = [e for e in all_cal_events if event_local_date(e, tz_offset) == today]
    week_events  = [e for e in all_cal_events
                    if today < event_local_date(e, tz_offset) <= today + timedelta(days=7)]

    # ── Weather ───────────────────────────────────────────────────────────────
    weather = fetch_weather(lat, lon) if lat is not None and lon is not None else None

    return {
        "today_cards":    today_cards,
        "week_cards":     week_cards,
        "today_events":   today_events,
        "week_events":    week_events,
        "all_cal_events": all_cal_events,
        "habits":         habits,
        "observations":   observations,
        "health_ctx":     health_ctx,
        "steps_today":    steps_today,
        "eng_prs":        eng_prs,
        "eng_issues":     eng_issues,
        "weather":        weather,
        "local_now":      local_now,
    }


# ── Scheduled (non-streaming) briefing generation ─────────────────────────────

def generate_today_briefing(today: date, tz_offset: int = 0) -> str | None:
    """Generate the today briefing as a plain string (used by the Telegram scheduler)."""
    d = _fetch_briefing_data(today, tz_offset)

    today_h = _today_hash(d["today_cards"], d["today_events"], d["habits"],
                          d["weather"] is not None, d["local_now"], d["steps_today"])
    cached = _cache_get("today", today_h)
    if cached:
        return cached.text

    pending_habits = [h for h in d["habits"] if not h.completed_today]
    if not d["today_cards"] and not d["today_events"] and not pending_habits:
        text = "Nothing scheduled today."
        _cache_set("today", today_h, text)
        return text

    today_ctx = build_today_context(
        d["today_cards"], d["today_events"], today, d["habits"],
        d["observations"], tz_offset, d["eng_prs"],
        local_now=d["local_now"], weather=d["weather"],
        all_cal_events=d["all_cal_events"], health_context=d["health_ctx"],
    )
    try:
        resp = llm_client().chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _TODAY_SYSTEM},
                {"role": "user",   "content": today_ctx},
            ],
            stream=False,
            temperature=0.1,
        )
        text = resp.choices[0].message.content.strip()
        _cache_set("today", today_h, text)
        return text
    except Exception as e:
        print(f"[briefing] LLM error: {e}")
        return None


# ── Briefing endpoint ─────────────────────────────────────────────────────────

@router.post("/api/briefing/stream")
def stream_briefing(request: Request, req: schemas.BriefingRequest, db: Session = Depends(get_db)):
    today_dt  = local_date(request)
    tz_offset = req.utc_offset_minutes or 0

    d         = _fetch_briefing_data(today_dt, tz_offset, req.lat, req.lon, db=db)
    local_now = d["local_now"]

    today_h = _today_hash(d["today_cards"], d["today_events"], d["habits"],
                          d["weather"] is not None, local_now, d["steps_today"])
    week_h  = _week_hash(d["week_cards"], d["week_events"])

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

    need_week  = not req.today_only
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

    pending_habits = [h for h in d["habits"] if not h.completed_today]
    today_ctx = build_today_context(
        d["today_cards"], d["today_events"], today_dt, d["habits"],
        d["observations"], tz_offset, d["eng_prs"],
        local_now=local_now, weather=d["weather"],
        all_cal_events=d["all_cal_events"], health_context=d["health_ctx"],
    )
    week_ctx = build_week_context(
        d["week_cards"], d["week_events"], today_dt, tz_offset, d["eng_issues"]
    ) if need_week else None

    def generate():
        weather_raw: str | None = None

        if cached_today is not None:
            if cached_weather:
                yield f"data: {cached_weather}\n\n"
            yield f"data: {json.dumps({'section': 'today', 'text': cached_today})}\n\n"
        else:
            if d["weather"]:
                weather_raw = json.dumps({'type': 'weather', **d["weather"]})
                yield f"data: {weather_raw}\n\n"

            today_acc: list[str] = []
            if not (d["today_cards"] or d["today_events"] or pending_habits):
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


# ── Standalone weather endpoint ───────────────────────────────────────────────

@router.get("/weather")
def get_weather(lat: float, lon: float):
    """Return current weather for the given coordinates."""
    result = fetch_weather(lat, lon)
    if result is None:
        return {}
    return result


# ── Assistant endpoint ────────────────────────────────────────────────────────

_ASSIST_TAVILY_KEY = os.getenv("TAVILY_API_KEY", "")


_geocode_cache: dict[tuple, str | None] = {}


def _reverse_geocode(lat: float, lon: float) -> str | None:
    # Round to ~1 km precision for cache key
    key = (round(lat, 2), round(lon, 2))
    if key in _geocode_cache:
        return _geocode_cache[key]
    try:
        r = http_requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "quantum-task/1.0"},
            timeout=5,
        )
        addr = r.json().get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county")
        state = addr.get("state")
        country = addr.get("country_code", "").upper()
        parts = [p for p in [city, state] if p]
        if country and country != "US":
            parts.append(country)
        result = ", ".join(parts) if parts else None
    except Exception:
        result = None
    _geocode_cache[key] = result
    return result


def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    if not _ASSIST_TAVILY_KEY:
        return []
    try:
        r = http_requests.post(
            "https://api.tavily.com/search",
            json={"api_key": _ASSIST_TAVILY_KEY, "query": query, "max_results": max_results},
            timeout=15,
        )
        r.raise_for_status()
        return [
            {"title": res.get("title", ""), "url": res.get("url", ""), "content": res.get("content", "")}
            for res in r.json().get("results", [])
        ]
    except Exception:
        return []


_ASSIST_DECISION_SYSTEM = """\
Decide whether answering this request requires current web data \
(local businesses, real-time info, current events, prices, reviews, hours, etc.).
Return ONLY valid JSON — no markdown.
If search needed: {"search": true, "queries": ["specific query 1", "specific query 2"]}
If not needed: {"search": false}
Use 1–3 targeted queries. Only include a location in queries if the user explicitly \
provided one — never assume or infer a location.
IMPORTANT: If the request involves finding specific places or businesses (hotels, \
restaurants, stores) but no location is given, return {"search": false} — the \
assistant will ask the user for their location instead of guessing.
"""

_ASSIST_SYSTEM = """\
You are a personal administrative assistant. You produce content the user can act on — \
you do not perform actions yourself.

What "producing content" means:
- Request is a reply to send → write the reply text, ready to copy and send
- Request is meeting prep → produce the agenda, talking points, or briefing notes
- Request is a summary → write the summary
- Request is extracting action items → list them clearly and concisely
- Request is drafting a document → write the document
- Request is finding local options → list real options from the web search results provided

Rules:
- Do not use first person for things you cannot do ("I will call", "I will book", \
"I have sent"). You produce drafts and information; the user takes the physical action.
- Do not explain your reasoning or what you are about to do. Produce the output directly.
- Match the tone and format implied by the request and context.
- If the context is a message the user received, the output is addressed to the sender.
- Keep output concise and professional unless the task implies otherwise.
- CRITICAL: Never invent specific facts — business names, hotel names, addresses, phone \
numbers, prices, people — that are not explicitly present in the provided context or \
web search results. If location-specific information is needed but no location was \
given, say: "I don't have your location — please tell me where you are and I'll help \
from there." If other key information is missing, ask for it in one sentence.
- If web search results are provided, use them as the primary source of truth. \
Cite sources inline when useful (e.g. "Source: https://example.com").
- Do not use markdown formatting. No asterisks for bold, no # headers, no --- dividers. \
Use plain text with line breaks and simple punctuation for structure.
"""


@router.post("/api/assist/stream")
def stream_assist(req: schemas.AssistRequest):
    location_str = None
    if req.lat is not None and req.lon is not None:
        location_str = _reverse_geocode(req.lat, req.lon)

    user_msg_parts = [f"Task: {req.card_title}"]
    if req.card_description:
        user_msg_parts.append(f"Additional context from card: {req.card_description}")
    if location_str:
        user_msg_parts.append(f"User's location: {location_str}")
    user_msg_parts.append(f"\nContext provided by user:\n{req.context}")
    user_msg = "\n".join(user_msg_parts)

    def generate():
        search_context = ""
        if _ASSIST_TAVILY_KEY:
            try:
                decision_resp = llm_client().chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": _ASSIST_DECISION_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    max_tokens=150,
                    temperature=0,
                    timeout=10,
                )
                decision = json.loads(decision_resp.choices[0].message.content.strip())
                if decision.get("search"):
                    yield f"data: {json.dumps({'status': 'searching'})}\n\n"
                    all_results = []
                    for q in decision.get("queries", [])[:3]:
                        all_results.extend(_tavily_search(q))
                    if all_results:
                        parts = [
                            f"[{r['title']}]({r['url']})\n{r['content']}"
                            for r in all_results[:8]
                        ]
                        search_context = "\n\n---\n\n".join(parts)
            except Exception:
                pass  # fall through to regular generation

        final_user = user_msg
        if search_context:
            final_user += f"\n\n--- Web Search Results ---\n{search_context}"

        try:
            stream = llm_client().chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": _ASSIST_SYSTEM},
                    {"role": "user",   "content": final_user},
                ],
                stream=True,
                temperature=0.3,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield f"data: {json.dumps({'text': delta})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/api/assist/global")
def global_assist(req: schemas.GlobalAssistRequest):
    """Header-level assistant: fetches card context by section or tag, then streams a response."""
    location_str = None
    if req.lat is not None and req.lon is not None:
        location_str = _reverse_geocode(req.lat, req.lon)

    # Build card context from selected section or tag
    card_context_parts: list[str] = []
    with SessionLocal() as db:
        if req.section:
            cards = (
                db.query(models.Card)
                .filter(
                    models.Card.section == req.section,
                    models.Card.archived == False,  # noqa: E712
                    models.Card.completed == False,  # noqa: E712
                )
                .order_by(models.Card.created_at)
                .limit(20)
                .all()
            )
            if cards:
                label_map = {
                    "today": "Today", "week": "This Week",
                    "month": "This Month", "later": "Stash",
                }
                label = label_map.get(req.section, req.section)
                card_context_parts.append(f"### Cards in '{label}'")
                for c in cards:
                    card_context_parts.append(
                        f"- **{c.title}**" + (f": {c.description}" if c.description else "")
                    )
        elif req.tag_id:
            tag = db.query(models.Tag).filter(models.Tag.id == req.tag_id).first()
            tag_cards = (
                db.query(models.Card)
                .join(models.card_tags, models.Card.id == models.card_tags.c.card_id)
                .filter(
                    models.card_tags.c.tag_id == req.tag_id,
                    models.Card.archived == False,  # noqa: E712
                    models.Card.completed == False,  # noqa: E712
                )
                .limit(20)
                .all()
            )
            if tag_cards:
                label = tag.name if tag else f"tag #{req.tag_id}"
                card_context_parts.append(f"### Cards tagged '{label}'")
                for c in tag_cards:
                    card_context_parts.append(
                        f"- **{c.title}**" + (f": {c.description}" if c.description else "")
                    )

        # Always inject habits and health context
        background_parts: list[str] = []
        today = date.today()
        today_str = today.isoformat()
        _, health_ctx = build_health_context(db, today)
        if health_ctx:
            background_parts.append(health_ctx)
        all_habits = db.query(models.Habit).filter(models.Habit.archived == False).all()  # noqa: E712
        completed_habit_ids = {
            c.habit_id for c in db.query(models.HabitCompletion)
            .filter(models.HabitCompletion.date == today_str).all()
        }
        pending_habits = [h.name for h in all_habits if h.id not in completed_habit_ids]
        done_habits    = [h.name for h in all_habits if h.id in completed_habit_ids]
        if pending_habits:
            background_parts.append("Habits pending today: " + ", ".join(pending_habits))
        if done_habits:
            background_parts.append("Habits completed today: " + ", ".join(done_habits))

    user_msg_parts = [f"Request: {req.prompt}"]
    if location_str:
        user_msg_parts.append(f"User's location: {location_str}")
    if background_parts:
        user_msg_parts.append("\n## User context (today)\n" + "\n".join(background_parts))
    if card_context_parts:
        user_msg_parts.append("\n" + "\n".join(card_context_parts))
    user_msg = "\n".join(user_msg_parts)

    def generate():
        search_context = ""
        if _ASSIST_TAVILY_KEY:
            try:
                decision_resp = llm_client().chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": _ASSIST_DECISION_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    max_tokens=150,
                    temperature=0,
                    timeout=10,
                )
                decision = json.loads(decision_resp.choices[0].message.content.strip())
                if decision.get("search"):
                    yield f"data: {json.dumps({'status': 'searching'})}\n\n"
                    all_results = []
                    for q in decision.get("queries", [])[:3]:
                        all_results.extend(_tavily_search(q))
                    if all_results:
                        parts = [
                            f"[{r['title']}]({r['url']})\n{r['content']}"
                            for r in all_results[:8]
                        ]
                        search_context = "\n\n---\n\n".join(parts)
            except Exception:
                pass

        final_user = user_msg
        if search_context:
            final_user += f"\n\n--- Web Search Results ---\n{search_context}"

        try:
            stream = llm_client().chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": _ASSIST_SYSTEM},
                    {"role": "user",   "content": final_user},
                ],
                stream=True,
                temperature=0.3,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield f"data: {json.dumps({'text': delta})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

