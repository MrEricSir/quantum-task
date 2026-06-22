import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, time as dt_time, timezone
from typing import List

import requests as http_requests
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

import models
import schemas
from database import SessionLocal
from deps import get_db, llm_client, LLM_MODEL, local_date
from health_context import build_health_context

router = APIRouter()

# ── Time / event helpers ─────────────────────────────────────────────────────

def _fmt_time(dt: datetime, utc_offset_minutes: int = 0) -> str:
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None) - timedelta(minutes=utc_offset_minutes)
    return dt.strftime("%I:%M %p").lstrip("0")


def _event_local_date(e, utc_offset_minutes: int) -> date:
    """Return the calendar event's date in the client's local timezone."""
    if e.all_day:
        return e.start.date() if hasattr(e.start, "date") else e.start
    local_dt = e.start.replace(tzinfo=None) - timedelta(minutes=utc_offset_minutes)
    return local_dt.date()


# ── Weather ──────────────────────────────────────────────────────────────────

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
_WMO_DESC = {
    0: "clear skies",
    1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "light showers", 81: "showers", 82: "heavy showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "severe thunderstorms",
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
        code     = int(cw.get("weathercode", 0))
        windy    = float(cw.get("windspeed", 0)) > 25
        emoji    = _WMO_EMOJI.get(code, "🌡️")
        if windy:
            emoji += "💨"
        description = _WMO_DESC.get(code, "mixed conditions")
        _RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 98, 99}
        _SNOW_CODES = {71, 73, 75, 77, 85, 86}
        return {
            "emojis": emoji, "high": high, "low": low,
            "description": description, "windy": windy,
            "umbrella": code in _RAIN_CODES,
            "snow": code in _SNOW_CODES,
            "cold": high < 45,
        }
    except Exception:
        return None


# ── Observations ─────────────────────────────────────────────────────────────

def _compute_observations(db: Session, today: date) -> str | None:
    """Derive plain-text behavioral observations from the last 30 days."""
    cutoff_str = (today - timedelta(days=30)).isoformat()
    cutoff_dt  = datetime.combine(today - timedelta(days=30), dt_time.min)
    lines: list[str] = []

    habits = db.query(models.Habit).filter(models.Habit.archived == False).all()  # noqa: E712
    for habit in habits:
        created = habit.created_at.date() if habit.created_at else today
        days_old = (today - created).days
        if days_old < 14:
            continue

        window = min(30, days_old)
        done_dates = {
            c.date for c in db.query(models.HabitCompletion).filter(
                models.HabitCompletion.habit_id == habit.id,
                models.HabitCompletion.date >= cutoff_str,
            ).all()
        }
        rate = len(done_dates) / window

        if rate < 0.5:
            lines.append(
                f'Habit "{habit.name}" completion: {round(rate * 100)}% over the past {window} days.'
            )
            continue

        by_wd: dict[str, dict] = defaultdict(lambda: {"done": 0, "total": 0})
        for i in range(window):
            d = today - timedelta(days=i + 1)
            wd = d.strftime("%A")
            by_wd[wd]["total"] += 1
            if d.isoformat() in done_dates:
                by_wd[wd]["done"] += 1

        worst_day, worst_rate = None, 1.0
        for wd, counts in by_wd.items():
            if counts["total"] >= 3:
                wd_rate = counts["done"] / counts["total"]
                if wd_rate < (rate - 0.40) and wd_rate < worst_rate:
                    worst_rate, worst_day = wd_rate, wd
        if worst_day:
            lines.append(
                f'You often skip "{habit.name}" on {worst_day}s '
                f'({round(worst_rate * 100)}% vs {round(rate * 100)}% average).'
            )

    overdue = db.query(models.Card).filter(
        models.Card.completed == False,  # noqa: E712
        models.Card.scheduled_at.isnot(None),
        models.Card.scheduled_at < datetime.combine(today, dt_time.min),
    ).count()
    if overdue >= 3:
        lines.append(f"You have {overdue} overdue scheduled tasks.")

    created_n = db.query(models.Card).filter(
        models.Card.created_at >= cutoff_dt,
    ).count()
    completed_n = db.query(models.Card).filter(
        models.Card.completed == True,  # noqa: E712
        models.Card.completed_at.isnot(None),
        models.Card.completed_at >= cutoff_dt,
    ).count()
    if created_n >= 10:
        cr = completed_n / created_n
        if cr < 0.4:
            lines.append(
                f"Task completion rate: {round(cr * 100)}% over the past 30 days "
                f"({completed_n} of {created_n} completed)."
            )

    return "\n".join(lines) if lines else None


# ── Context builders ──────────────────────────────────────────────────────────

def _build_today_context(
    todos: list,
    cal_events: list,
    today: date,
    habits: list = None,
    observations: str | None = None,
    utc_offset_minutes: int = 0,
    eng_prs: list = None,
    local_now: datetime | None = None,
    weather: dict | None = None,
    all_cal_events: list = None,
    health_context: str | None = None,
) -> str:
    now = local_now or datetime.now()
    time_str = now.strftime("%-I:%M %p").lstrip("0") if hasattr(now, "strftime") else ""
    lines = [f"Current time: {time_str} on {today.strftime('%A, %B %d, %Y')}"]

    if weather:
        w_line = f"Weather: {weather['description']}, high {weather['high']}°F / low {weather['low']}°F"
        w_actions = []
        if weather.get("umbrella"):
            w_actions.append("rain — advise bringing an umbrella")
        if weather.get("snow"):
            w_actions.append("snow — mention it")
        if weather.get("cold"):
            w_actions.append("cold — advise dressing warmly")
        if weather.get("windy"):
            w_actions.append("very windy — worth mentioning")
        if w_actions:
            w_line += f". Action: {'; '.join(w_actions)}"
        lines.append(w_line)

    recurring_titles: set[str] = set()
    if all_cal_events:
        title_days: dict[str, set] = {}
        for e in all_cal_events:
            day = _event_local_date(e, utc_offset_minutes)
            title_days.setdefault(e.title, set()).add(day)
        recurring_titles = {t for t, days in title_days.items() if len(days) >= 2}

    # OOO events are pre-filtered by the caller and never appear here.
    upcoming_events = []
    for e in cal_events:
        if e.all_day:
            upcoming_events.append(e)
        else:
            local_start = e.start.replace(tzinfo=None) - timedelta(minutes=utc_offset_minutes) \
                if e.start.tzinfo else e.start
            if local_start >= now:
                upcoming_events.append(e)

    if upcoming_events:
        lines.append("Upcoming events:")
        for e in upcoming_events:
            recurring_tag = " (recurring)" if e.title in recurring_titles else ""
            if e.all_day:
                lines.append(f"  - {e.title} (all day){recurring_tag}")
            else:
                lines.append(f"  - {e.title} at {_fmt_time(e.start, utc_offset_minutes)}{recurring_tag}")

    if todos:
        lines.append("Tasks for today:")
        for t in todos:
            suffix = f" at {_fmt_time(t.scheduled_at, utc_offset_minutes)}" if t.scheduled_at else ""
            overdue = f" [OVERDUE by {t.overdue_days} day{'s' if t.overdue_days != 1 else ''}]" if t.overdue_days > 0 else ""
            lines.append(f"  - {t.title}{suffix}{overdue}")

    if eng_prs:
        lines.append(f"GitHub PRs awaiting your review: {len(eng_prs)}")

    pending_habits = [h.name for h in (habits or []) if not h.completed_today]
    if pending_habits:
        lines.append("Habits not yet done today:")
        for name in pending_habits:
            lines.append(f"  - {name}")

    if not upcoming_events and not todos and not eng_prs and not pending_habits:
        lines.append("Nothing remaining on the schedule.")

    if health_context:
        lines.append(health_context)

    if observations:
        lines.append("Patterns (use at most one if genuinely relevant):")
        lines.append(observations)

    return "\n".join(lines)


def _build_week_context(todos: list, cal_events: list, today: date, utc_offset_minutes: int = 0, eng_issues: list = None) -> str | None:
    if not todos and not cal_events:
        return None

    recur_groups: dict[tuple, list] = {}
    for e in cal_events:
        start = e.start
        day = _event_local_date(e, utc_offset_minutes)
        if day <= today:
            continue
        time_key = "all_day" if e.all_day else _fmt_time(start, utc_offset_minutes)
        recur_groups.setdefault((e.title, time_key), []).append((day, start, e.all_day))
    recurring_keys = {k for k, v in recur_groups.items() if len(v) >= 2}

    by_day: dict[date, list[tuple]] = {}
    unscheduled: list[str] = []

    for e in cal_events:
        start = e.start
        day = _event_local_date(e, utc_offset_minutes)
        if day <= today:
            continue
        time_key = "all_day" if e.all_day else _fmt_time(start, utc_offset_minutes)
        if (e.title, time_key) in recurring_keys:
            continue
        if e.all_day:
            by_day.setdefault(day, []).append((None, f"- {e.title} (all day)"))
        else:
            by_day.setdefault(day, []).append((start, f"- {e.title} at {_fmt_time(start, utc_offset_minutes)}"))

    for t in todos:
        if t.scheduled_at:
            day = t.scheduled_at.date()
            if day <= today:
                continue
            by_day.setdefault(day, []).append((t.scheduled_at, f"- {t.title} at {_fmt_time(t.scheduled_at, utc_offset_minutes)}"))
        else:
            unscheduled.append(f"- {t.title}")

    if not by_day and not unscheduled and not recurring_keys and not eng_issues:
        return None

    tomorrow = today + timedelta(days=1)
    lines = [f"Week ahead starting {tomorrow.strftime('%A, %B %d, %Y')}:"]

    if recurring_keys:
        lines.append("\nRecurring this week (mention ONCE at the start, never repeat per day):")
        for (title, time_key), occurrences in sorted(recur_groups.items(), key=lambda x: x[0][0]):
            if (title, time_key) not in recurring_keys:
                continue
            day_names = ", ".join(d.strftime("%a") for d, _, _ in sorted(occurrences, key=lambda x: x[0]))
            if time_key == "all_day":
                lines.append(f"  - {title} (all day) — {day_names}")
            else:
                lines.append(f"  - {title} at {time_key} — {day_names}")

    for day in sorted(by_day):
        lines.append(f"\n{day.strftime('%A, %B %d')}:")
        items = sorted(by_day[day], key=lambda x: (x[0] is not None, x[0] or datetime.min))
        for _, text in items:
            lines.append(f"  {text}")

    if unscheduled:
        lines.append("\nNo specific day:")
        for item in unscheduled:
            lines.append(f"  {item}")

    if eng_issues:
        lines.append(f"\nAssigned GitHub issues: {len(eng_issues)}")

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


def _today_hash(todos: list, events: list, habits: list, has_location: bool, local_now: datetime | None = None, steps_today: int | None = None) -> str:
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
        "time_bucket": _time_of_day_bucket(local_now) if local_now else -1,
        "steps_today": steps_today,
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


# ── Briefing endpoint ─────────────────────────────────────────────────────────

@router.post("/api/briefing/stream")
def stream_briefing(request: Request, req: schemas.BriefingRequest):
    today_dt = local_date(request)
    tz_offset = req.utc_offset_minutes or 0
    local_now = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)

    today_todos  = [t for t in req.todos if t.section == "today"]
    today_events = [e for e in req.calendar_events if _event_local_date(e, tz_offset) == today_dt and not e.is_ooo]
    week_todos   = [t for t in req.todos if t.section == "week"]
    week_events  = [e for e in req.calendar_events
                    if today_dt < _event_local_date(e, tz_offset) <= today_dt + timedelta(days=7) and not e.is_ooo]

    with SessionLocal() as _pre_db:
        _health_data, _health_ctx = build_health_context(_pre_db, today_dt)
    _steps_today = int(_health_data["today"].get("steps", 0)) or None

    today_h = _today_hash(today_todos, today_events, req.habits, req.lat is not None, local_now, _steps_today)
    week_h  = _week_hash(week_todos, week_events)

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

    pending_habits = [h for h in req.habits if not h.completed_today]
    weather = _fetch_weather(req.lat, req.lon) if req.lat is not None and req.lon is not None else None
    with SessionLocal() as _obs_db:
        observations = _compute_observations(_obs_db, today_dt)
        eng_items = _obs_db.query(models.EngineeringItem).filter_by(state="open").all()
    on_board = {t.description for t in req.todos if t.description}
    eng_prs    = [i for i in eng_items if i.item_type == "pr"    and i.url not in on_board]
    eng_issues = [i for i in eng_items if i.item_type == "issue" and i.url not in on_board]
    today_ctx = _build_today_context(today_todos, today_events, today_dt, req.habits, observations, tz_offset, eng_prs, local_now=local_now, weather=weather, all_cal_events=req.calendar_events, health_context=_health_ctx)
    week_ctx  = _build_week_context(week_todos, week_events, today_dt, tz_offset, eng_issues) if need_week else None

    def generate():
        weather_raw: str | None = None

        if cached_today is not None:
            if cached_weather:
                yield f"data: {cached_weather}\n\n"
            yield f"data: {json.dumps({'section': 'today', 'text': cached_today})}\n\n"
        else:
            if weather:
                weather_raw = json.dumps({'type': 'weather', **weather})
                yield f"data: {weather_raw}\n\n"

            today_acc: list[str] = []
            if not (today_todos or today_events or pending_habits):
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


# ── Assistant endpoint ────────────────────────────────────────────────────────

_ASSIST_SYSTEM = """\
You are a personal administrative assistant. When given a task and context, you take \
action — you do not describe what you would do, you just do it.

Examples of what "taking action" means:
- Task is a reply to send → write the reply, ready to copy and send
- Task is meeting prep → produce the agenda, talking points, or briefing notes
- Task is a summary → write the summary
- Task is extracting action items → list them clearly and concisely
- Task is drafting a document → write the document

Rules:
- Do not explain your reasoning or what you are about to do. Produce the output directly.
- Match the tone and format implied by the task and context.
- If the context is a message the user received, the output is addressed to the sender.
- Keep output concise and professional unless the task implies otherwise.
- If the context does not contain enough information to act, say so in one sentence.
"""


@router.post("/api/assist/stream")
def stream_assist(req: schemas.AssistRequest):
    user_msg_parts = [f"Task: {req.task_title}"]
    if req.task_description:
        user_msg_parts.append(f"Additional context from task: {req.task_description}")
    user_msg_parts.append(f"\nContext provided by user:\n{req.context}")
    user_msg = "\n".join(user_msg_parts)

    def generate():
        try:
            stream = llm_client().chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": _ASSIST_SYSTEM},
                    {"role": "user",   "content": user_msg},
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


# ── Daily Plan endpoint ───────────────────────────────────────────────────────

def _fmt_time_24h(dt: datetime, utc_offset_minutes: int = 0) -> str:
    """Return local time as HH:MM (24h) for use in LLM prompts."""
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None) - timedelta(minutes=utc_offset_minutes)
    return dt.strftime("%H:%M")


_TIME_12H_RE = re.compile(
    r'^(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(AM|PM)$', re.IGNORECASE
)
_TIME_24H_RE = re.compile(r'^(\d{1,2}):(\d{2})(?::\d{2})?$')


def _normalize_plan_time(t) -> str | None:
    """Normalise whatever the LLM returns as a time value to 'HH:MM' (24h)."""
    if not t:
        return None
    t = str(t).strip()
    m = _TIME_12H_RE.match(t)
    if m:
        h = int(m.group(1))
        mins = m.group(2) or "00"
        period = m.group(3).upper()
        if period == "PM" and h != 12:
            h += 12
        elif period == "AM" and h == 12:
            h = 0
        return f"{h:02d}:{mins}"
    m = _TIME_24H_RE.match(t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


_DAILY_PLAN_SYSTEM = """\
You are a daily scheduler. Your ONLY job is to assign times to the items provided — \
nothing more. Every block's title must be copied character-for-character from the input list.

Return ONLY a valid JSON object: {"blocks": [...]}

Each block has these fields:
  time     - "HH:MM" 24h local time when the block starts, or null if it can't fit today
  duration - integer minutes
  title    - EXACT name copied from the input (never paraphrase or invent)
  type     - one of: "event" | "task" | "habit" | "walk" | "break"
  fixed    - true for calendar events, false for everything else
  note     - one short phrase (3-5 words) of rationale, or null

Scheduling rules:
1. Calendar events AND tasks with a fixed start time are FIXED — the block's time must equal their stated start time exactly. Never shift them earlier or later.
2. Estimate durations realistically: 15 min for quick tasks, 30-60 min typical, longer for complex work.
3. Fill gaps between fixed items with flexible tasks, starting from the current time.
4. Add a 10-min break block after 90+ consecutive minutes of focused work.
5. Tasks that cannot fit today appear at the end with time=null.
6. The only allowed block types are items from the input lists plus optional break and walk blocks.
7. If a "Health data" section shows steps below 60% of goal, add ONE "Walk" block (20-30 min) \
in an open afternoon slot. Only add it if a genuinely free slot exists — never displace fixed items.
"""


def _build_daily_plan_context(
    today_dt, today_events: list, today_todos: list, pending_habits: list, tz_offset: int,
    health_context: str | None = None,
) -> str:
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    lines = [
        f"Current local time: {now_local.strftime('%H:%M')}",
        f"Date: {today_dt.strftime('%A, %B %d, %Y')}",
    ]

    if today_events:
        lines.append("\nCalendar events (FIXED — do not move):")
        for e in today_events:
            if e.all_day:
                lines.append(f"  - {e.title} (all day)")
            else:
                start_s = _fmt_time_24h(e.start, tz_offset)
                end_s = _fmt_time_24h(e.end, tz_offset) if e.end else None
                range_s = f"{start_s}–{end_s}" if end_s else start_s
                lines.append(f"  - {e.title} at {range_s}")

    timed_todos   = [t for t in today_todos if t.scheduled_at]
    untimed_todos = [t for t in today_todos if not t.scheduled_at]

    if timed_todos:
        lines.append("\nTasks with a fixed start time (treat exactly like calendar events — do not shift):")
        for t in timed_todos:
            lines.append(f"  - {t.title} starts at {_fmt_time_24h(t.scheduled_at, tz_offset)}")

    if untimed_todos:
        lines.append("\nUnscheduled tasks (find the best available slot):")
        for t in untimed_todos:
            lines.append(f"  - {t.title}")

    if pending_habits:
        lines.append("\nHabits still to complete today:")
        for h in pending_habits:
            lines.append(f"  - {h.name}")

    if health_context:
        lines.append("")
        lines.append(health_context)

    return "\n".join(lines)


@router.post("/api/daily-plan")
def generate_daily_plan(request: Request, req: schemas.BriefingRequest):
    today_dt = local_date(request)
    tz_offset = req.utc_offset_minutes or 0

    today_todos = [t for t in req.todos if t.section == "today" and not t.completed]
    today_events = sorted(
        [e for e in req.calendar_events if _event_local_date(e, tz_offset) == today_dt and not e.is_ooo],
        key=lambda e: e.start,
    )
    pending_habits = [h for h in req.habits if not h.completed_today]

    if not today_todos and not today_events and not pending_habits:
        return {"blocks": []}

    with SessionLocal() as _hdb:
        health_data, health_ctx = build_health_context(_hdb, today_dt)

    # Allow the AI to add a Walk block when steps are well below goal
    steps_today = health_data["today"].get("steps")
    steps_goal = health_data["goals"].get("steps")
    walk_eligible = (
        steps_today is not None and steps_goal is not None and steps_today < steps_goal * 0.6
    )

    context = _build_daily_plan_context(
        today_dt, today_events, today_todos, pending_habits, tz_offset,
        health_context=health_ctx,
    )

    valid_titles = (
        {e.title for e in today_events}
        | {t.title for t in today_todos}
        | {h.name for h in pending_habits}
    )
    if walk_eligible:
        valid_titles.add("Walk")

    try:
        response = llm_client().chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _DAILY_PLAN_SYSTEM},
                {"role": "user",   "content": context},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        blocks = result.get("blocks", [])
        validated = []
        for block in blocks:
            block["time"] = _normalize_plan_time(block.get("time"))
            if block.get("type") == "break" or block.get("title") in valid_titles:
                validated.append(block)
        return {"blocks": validated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
