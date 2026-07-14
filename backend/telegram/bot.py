"""
Telegram bot message routing and reply generation.

Entry point: handle_update(update_dict) — call this from the webhook endpoint.
All DB sessions are opened/closed inside each function; no session is passed in.
"""
from __future__ import annotations

import json as _json
import traceback
from datetime import datetime, timezone, timedelta

import models
from briefing.context import event_local_date
from database import SessionLocal
from deps import llm_client, LLM_MODEL
from gcal import _cached_fetch_events
from settings import Settings
from telegram.notify import send_message


# ── Intent prompt ─────────────────────────────────────────────────────────────

_TELEGRAM_INTENT_PROMPT = """\
You are the intent parser for a personal productivity Telegram bot.
Given the user's message, return a single JSON object describing the action to take.

Reference dates:
  Today    : {today} ({weekday})
  Tomorrow : {tomorrow}

{tags_section}

The "action" field must be one of:

  "query_schedule"
      User is asking about their tasks or calendar for a specific day.
      Also return "date": the resolved date as YYYY-MM-DD.
      Default to today if no day is mentioned.
      Examples: "what's tomorrow?", "what do I have on Wednesday?",
                "what's first up tomorrow morning?", "anything on Friday?",
                "am I busy Thursday?", "what's on my plate today?"

  "query_habits"
      User wants to see their habit status for today.
      Examples: "how are my habits?", "habit check", "did I do everything?"

  "query_overdue"
      User wants to see overdue tasks.
      Examples: "what's overdue?", "anything late?", "what have I missed?"

  "capture"
      User is stating a new task, event, or item to save.
      Also return:
        "title"          — task name (preserve names and key context; strip date/time phrases)
        "section"        — "today" | "week" | "month" | "later"  (default: "later")
        "scheduled_at"   — ISO 8601 datetime if a specific time was mentioned, else null
        "suggested_tags" — list of matching tag names from available tags, or []
        "description"    — verbatim extra context from the input, or null
      Examples: "call dentist", "buy groceries", "meeting with Sarah at 3pm tomorrow",
                "dentist appointment next Friday at 2pm"

  "mark_complete"
      User is marking an existing task done.
      Also return "match_query": the task title or fragment to match.
      Examples: "done with dentist", "finished the report", "mark groceries complete"

  "undo"
      User wants to reverse their last action (capture or mark_complete).
      Examples: "undo", "undo that", "wait, cancel that", "actually never mind"

  "query_week"
      User wants an overview of the week ahead (next 7 days).
      Examples: "what's this week look like?", "week ahead", "what's coming up?",
                "show me my week", "anything this week?"

  "query_health"
      User wants health or fitness data: steps, weight, body fat, heart rate, etc.
      Examples: "how many steps today?", "how's my health?", "what's my step count?",
                "am I near my step goal?", "how's my weight doing?"

  "query_streaks"
      User wants habit streak information.
      Examples: "what are my streaks?", "how long is my meditation streak?",
                "how many days in a row?", "habit streaks"

  "query_priority"
      User wants a recommendation on what to work on or focus on next.
      Examples: "what should I work on?", "what's my priority?", "what should I do next?",
                "help me focus", "what's most important right now?"

Reply ONLY with valid JSON. No explanation.\
"""


# ── Undo state (in-memory, per chat) ─────────────────────────────────────────
# Stores the most recent reversible action per chat_id.
# {"type": "capture"|"mark_complete", "card_id": int, "title": str}
_last_actions: dict[str, dict] = {}


# ── LLM intent classification ─────────────────────────────────────────────────

def _parse_telegram_intent(text: str, tz_offset: int) -> dict:
    """Call the LLM with the Telegram intent prompt and return parsed JSON."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()
    tomorrow = today + timedelta(days=1)

    with SessionLocal() as db:
        tag_names = [t.name for t in db.query(models.Tag).order_by(models.Tag.name).all()]

    tags_section = f"Available tags: {', '.join(tag_names)}" if tag_names else "No tags defined."
    prompt = _TELEGRAM_INTENT_PROMPT.format(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        tomorrow=tomorrow.isoformat(),
        tags_section=tags_section,
    )

    client = llm_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        timeout=15,
    )
    return _json.loads(response.choices[0].message.content)


# ── Public entry point ────────────────────────────────────────────────────────

def handle_update(update: dict) -> None:
    """Process a single Telegram update. Called synchronously from the webhook endpoint."""
    try:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        text = (msg.get("text") or "").strip()
        if not text:
            print("[telegram] update has no text, skipping")
            return
        chat_id_incoming = str(msg.get("chat", {}).get("id", ""))

        with SessionLocal() as db:
            s = Settings(db)
            token     = s.telegram_token
            chat_id   = s.telegram_chat_id
            tz_offset = s.tz_offset

        print(f"[telegram] incoming chat_id={chat_id_incoming!r} configured={chat_id!r} token_set={bool(token)}")

        if not token or not chat_id:
            print("[telegram] bot not configured, dropping update")
            return
        if chat_id_incoming != chat_id:
            print(f"[telegram] chat_id mismatch: got {chat_id_incoming!r} expected {chat_id!r}")
            return

        print(f"[telegram] routing message: {text[:80]!r}")
        reply = _route_message(text, tz_offset, chat_id)
        print(f"[telegram] reply preview: {reply[:80]!r}")
        ok = send_message(token, chat_id, reply)
        print(f"[telegram] send_message ok={ok}")
    except Exception as exc:
        print(f"[telegram] unhandled error in handle_update: {exc}")
        print(traceback.format_exc())


# ── Routing ───────────────────────────────────────────────────────────────────

def _route_message(text: str, tz_offset: int, chat_id: str = "") -> str:
    """Classify the user's message via LLM and dispatch to the right handler."""
    lower = text.lower().strip()

    # Instant shortcuts — skip LLM for unambiguous single-word commands
    if lower in ("help", "/help", "/start"):
        return (
            "Hi! Here's what I can do:\n\n"
            "<b>today</b> — show today's tasks\n"
            "<b>tomorrow</b> — show tomorrow's schedule\n"
            "<b>week</b> — overview of the week ahead\n"
            "<b>habits</b> — habit status for today\n"
            "<b>streaks</b> — your current habit streaks\n"
            "<b>health</b> — steps, weight, and fitness data\n"
            "<b>overdue</b> — overdue tasks\n"
            "<b>priority</b> — what to focus on next\n"
            "<b>done [task]</b> — mark a task complete\n"
            "<b>undo</b> — reverse your last capture or completion\n"
            "<b>anything else</b> — I'll figure it out or capture it as a task\n\n"
            "You'll also get your daily briefing and reminders automatically."
        )
    if lower in ("today", "list", "tasks"):
        return _reply_today(tz_offset)
    if lower == "tomorrow":
        now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
        return _reply_date((now_local + timedelta(days=1)).date(), tz_offset)
    if lower in ("habits", "habit"):
        return _reply_habits(tz_offset)
    if lower == "overdue":
        return _reply_overdue(tz_offset)
    if lower in ("undo", "undo that", "cancel that", "never mind", "nevermind"):
        return _reply_undo(chat_id)
    if lower in ("week", "this week", "week ahead"):
        return _reply_week(tz_offset)
    if lower in ("health", "steps", "fitness"):
        return _reply_health(tz_offset)
    if lower in ("streaks", "streak"):
        return _reply_streaks(tz_offset)
    if lower in ("priority", "focus", "next"):
        return _reply_priority(tz_offset)

    # LLM intent classification for everything else
    print(f"[telegram] classifying intent: {text[:60]!r}")
    try:
        intent = _parse_telegram_intent(text, tz_offset)
    except Exception as e:
        print(f"[telegram] intent parse failed: {e}")
        print(traceback.format_exc())
        return _capture_plain(text, tz_offset, chat_id)

    action = intent.get("action", "capture")
    print(f"[telegram] intent={action!r} data={str(intent)[:120]}")

    if action == "undo":
        return _reply_undo(chat_id)

    if action == "query_week":
        return _reply_week(tz_offset)

    if action == "query_health":
        return _reply_health(tz_offset)

    if action == "query_streaks":
        return _reply_streaks(tz_offset)

    if action == "query_priority":
        return _reply_priority(tz_offset)

    if action == "query_schedule":
        now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
        today = now_local.date()
        date_str = intent.get("date")
        try:
            from datetime import date as _date
            target = _date.fromisoformat(date_str) if date_str else today
        except (ValueError, TypeError):
            target = today
        return _reply_date(target, tz_offset)

    if action == "query_habits":
        return _reply_habits(tz_offset)

    if action == "query_overdue":
        return _reply_overdue(tz_offset)

    if action == "mark_complete":
        query = intent.get("match_query") or ""
        if not query:
            return "What task should I mark complete? Try: <b>done [task name]</b>"
        return _reply_complete(query, chat_id)

    # "capture" or anything unrecognised
    return _capture_from_intent(intent, text, tz_offset, chat_id)


# ── Calendar helpers ──────────────────────────────────────────────────────────

def _fetch_cal_events_for_date(target_date, tz_offset: int) -> list:
    """Fetch calendar events from all configured iCal feeds for a given date."""
    import schemas
    now_utc = datetime.now(timezone.utc)
    events = []
    try:
        with SessionLocal() as db:
            mappings = db.query(models.CalendarMapping).all()
        week_end = target_date + timedelta(days=2)
        for m in mappings:
            try:
                for ev in _cached_fetch_events(m.ical_url, target_date, week_end):
                    if ev.get("is_ooo"):
                        continue
                    if not ev["all_day"]:
                        cutoff = ev.get("end") or ev["start"]
                        if cutoff < now_utc:
                            continue
                    cal_ev = schemas.CalendarEvent(
                        id=ev["id"], title=ev["title"],
                        start=ev["start"], end=ev.get("end"),
                        all_day=ev["all_day"], section="today", is_ooo=False,
                    )
                    if event_local_date(cal_ev, tz_offset) == target_date:
                        events.append(cal_ev)
            except Exception as e:
                print(f"[telegram] calendar fetch error for mapping {m.id}: {e}")
    except Exception as e:
        print(f"[telegram] calendar fetch error: {e}")
    return events


def _fmt_event_time(ev, tz_offset: int) -> str:
    if ev.all_day:
        return "all day"
    start = ev.start
    if start.tzinfo is not None:
        start = start.replace(tzinfo=None) - timedelta(minutes=tz_offset)
    return start.strftime("%-I:%M %p")


# ── Reply functions ───────────────────────────────────────────────────────────

def _reply_today(tz_offset: int) -> str:
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    with SessionLocal() as db:
        cards = (
            db.query(models.Card)
            .filter_by(section="today", completed=False, archived=False)
            .order_by(models.Card.position)
            .all()
        )

    cal_events = _fetch_cal_events_for_date(today, tz_offset)

    if not cards and not cal_events:
        return "✓ Nothing on your plate today. Enjoy!"

    overdue = [c for c in cards if c.scheduled_at and c.scheduled_at.date() < today]
    timed   = [c for c in cards if c.scheduled_at and c.scheduled_at.date() >= today]
    untimed = [c for c in cards if not c.scheduled_at]

    lines = ["<b>📋 Today</b>"]

    if overdue:
        lines.append("\n<b>⚠ Overdue</b>")
        for c in sorted(overdue, key=lambda x: x.scheduled_at):
            days = (today - c.scheduled_at.date()).days
            lines.append(f"• {c.title} ({days}d)")

    if cal_events:
        lines.append("\n<b>📅 Calendar</b>")
        for ev in sorted(cal_events, key=lambda e: (e.all_day, e.start)):
            lines.append(f"• {ev.title} @ {_fmt_event_time(ev, tz_offset)}")

    if timed:
        lines.append("\n<b>⏰ Scheduled tasks</b>")
        for c in sorted(timed, key=lambda x: x.scheduled_at):
            lines.append(f"• {c.title} @ {c.scheduled_at.strftime('%-I:%M %p')}")

    if untimed:
        lines.append("")
        for c in untimed:
            lines.append(f"• {c.title}")

    return "\n".join(lines)


def _reply_date(target_date, tz_offset: int) -> str:
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    if target_date == today:
        return _reply_today(tz_offset)

    with SessionLocal() as db:
        all_cards = (
            db.query(models.Card)
            .filter(
                models.Card.completed == False,  # noqa: E712
                models.Card.archived == False,   # noqa: E712
                models.Card.scheduled_at.isnot(None),
            )
            .all()
        )

    day_cards = [c for c in all_cards if c.scheduled_at.date() == target_date]
    cal_events = _fetch_cal_events_for_date(target_date, tz_offset)

    delta = (target_date - today).days
    label = f"Tomorrow — {target_date.strftime('%A, %b %-d')}" if delta == 1 else target_date.strftime('%A, %b %-d')

    if not day_cards and not cal_events:
        return f"Nothing scheduled for {label}."

    lines = [f"<b>📅 {label}</b>"]

    if cal_events:
        lines.append("\n<b>Calendar</b>")
        for ev in sorted(cal_events, key=lambda e: (e.all_day, e.start)):
            lines.append(f"• {ev.title} @ {_fmt_event_time(ev, tz_offset)}")

    if day_cards:
        lines.append("\n<b>Tasks</b>")
        for c in sorted(day_cards, key=lambda x: x.scheduled_at):
            lines.append(f"• {c.title} @ {c.scheduled_at.strftime('%-I:%M %p')}")

    return "\n".join(lines)


def _reply_habits(tz_offset: int) -> str:
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today_str = now_local.date().isoformat()

    with SessionLocal() as db:
        habits = db.query(models.Habit).filter_by(archived=False).order_by(models.Habit.id).all()
        if not habits:
            return "You don't have any habits set up yet."
        completed_ids = {
            r.habit_id for r in db.query(models.HabitCompletion).filter_by(date=today_str).all()
        }

    done    = [h for h in habits if h.id in completed_ids]
    pending = [h for h in habits if h.id not in completed_ids]
    total   = len(habits)

    if not pending:
        return f"✓ All {total} habit{'s' if total != 1 else ''} done for today!"

    lines = [f"<b>🔁 Habits — {len(done)}/{total} done</b>\n"]
    for h in done:
        lines.append(f"✓ {h.name}")
    for h in pending:
        lines.append(f"○ {h.name}")
    return "\n".join(lines)


def _reply_overdue(tz_offset: int) -> str:
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    with SessionLocal() as db:
        candidates = (
            db.query(models.Card)
            .filter(
                models.Card.completed == False,  # noqa: E712
                models.Card.archived == False,   # noqa: E712
                models.Card.section == "today",
                models.Card.scheduled_at.isnot(None),
            )
            .all()
        )
    overdue = [c for c in candidates if c.scheduled_at.date() < today]

    if not overdue:
        return "✓ No overdue tasks."

    lines = [f"<b>⚠ {len(overdue)} overdue task{'s' if len(overdue) != 1 else ''}</b>\n"]
    for c in sorted(overdue, key=lambda x: x.scheduled_at):
        days = (today - c.scheduled_at.date()).days
        lines.append(f"• {c.title} ({days}d)")
    return "\n".join(lines)


def _reply_complete(query: str, chat_id: str = "") -> str:
    query_lower = query.lower()

    with SessionLocal() as db:
        cards = db.query(models.Card).filter_by(completed=False, archived=False).all()
        match = None
        for c in cards:
            if c.title.lower() == query_lower:
                match = c
                break
        if not match:
            for c in cards:
                if query_lower in c.title.lower():
                    match = c
                    break

        if not match:
            return f'Couldn\'t find a task matching "{query}". Try <b>today</b> to see your list.'

        match.completed = True
        match.completed_at = datetime.now(timezone.utc)
        card_id = match.id
        title = match.title
        db.commit()

    if chat_id:
        _last_actions[chat_id] = {"type": "mark_complete", "card_id": card_id, "title": title}

    return f"✓ Marked complete: <b>{title}</b>\nSend <b>undo</b> to reverse."


def _capture_from_intent(intent: dict, original_text: str, tz_offset: int, chat_id: str = "") -> str:
    """Create a card from a 'capture' intent returned by the LLM."""
    section = intent.get("section") or "later"
    if section not in ("today", "week", "month", "later"):
        section = "later"
    title = (intent.get("title") or original_text).strip()

    scheduled_at = None
    raw_dt = intent.get("scheduled_at")
    if raw_dt:
        try:
            scheduled_at = datetime.fromisoformat(raw_dt)
        except (ValueError, TypeError):
            pass

    with SessionLocal() as db:
        max_pos = db.query(models.Card).filter_by(section=section).count()
        card = models.Card(
            title=title,
            description=intent.get("description"),
            section=section,
            scheduled_at=scheduled_at,
            position=max_pos,
        )
        tag_names = intent.get("suggested_tags") or []
        if tag_names:
            card.tags = db.query(models.Tag).filter(models.Tag.name.in_(tag_names)).all()
        db.add(card)
        db.commit()
        card_id = card.id
        section_label = {
            "today": "Today", "week": "This Week",
            "month": "This Month", "later": "Later",
        }.get(card.section, card.section)

    if chat_id:
        _last_actions[chat_id] = {"type": "capture", "card_id": card_id, "title": title}

    return f"✓ Added to <b>{section_label}</b>: {title}\nSend <b>undo</b> to remove it."


def _capture_plain(text: str, tz_offset: int, chat_id: str = "") -> str:
    """Fallback: create a plain card in Today when the LLM fails."""
    with SessionLocal() as db:
        pos = db.query(models.Card).filter_by(section="today").count()
        card = models.Card(title=text, section="today", position=pos)
        db.add(card)
        db.commit()
        card_id = card.id

    if chat_id:
        _last_actions[chat_id] = {"type": "capture", "card_id": card_id, "title": text}

    return f"✓ Added to <b>Today</b>: {text}\nSend <b>undo</b> to remove it."


def _reply_week(tz_offset: int) -> str:
    """Summarise the next 7 days: calendar events + scheduled tasks grouped by day."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    with SessionLocal() as db:
        week_cards = (
            db.query(models.Card)
            .filter(
                models.Card.completed == False,  # noqa: E712
                models.Card.archived == False,   # noqa: E712
                models.Card.scheduled_at.isnot(None),
            )
            .all()
        )

    # Group scheduled cards by day (next 7 days, not including today)
    by_day: dict = {}
    for c in week_cards:
        d = c.scheduled_at.date()
        if today < d <= today + timedelta(days=7):
            by_day.setdefault(d, {"cards": [], "events": []})["cards"].append(c)

    # Calendar events for the week
    for offset in range(1, 8):
        d = today + timedelta(days=offset)
        evs = _fetch_cal_events_for_date(d, tz_offset)
        if evs:
            by_day.setdefault(d, {"cards": [], "events": []})["events"].extend(evs)

    if not by_day:
        return "Nothing scheduled for the week ahead."

    lines = ["<b>📅 Week ahead</b>"]
    for d in sorted(by_day):
        label = "Tomorrow" if d == today + timedelta(days=1) else d.strftime("%A, %b %-d")
        day_lines = [f"\n<b>{label}</b>"]
        items = by_day[d]
        for ev in sorted(items["events"], key=lambda e: (e.all_day, e.start)):
            day_lines.append(f"  📅 {ev.title} @ {_fmt_event_time(ev, tz_offset)}")
        for c in sorted(items["cards"], key=lambda x: x.scheduled_at):
            day_lines.append(f"  • {c.title} @ {c.scheduled_at.strftime('%-I:%M %p')}")
        lines.extend(day_lines)

    return "\n".join(lines)


def _reply_health(tz_offset: int) -> str:
    """Return today's health data from Withings (steps, weight, body fat, etc.)."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    with SessionLocal() as db:
        has_credentials = db.query(models.WithingsCredentials).first() is not None
        from health_context import build_health_context
        data, ctx = build_health_context(db, today)

    if not has_credentials:
        return "Withings isn't connected yet. Link it in the app under Settings → Withings."

    if not ctx:
        return "No health data available yet. Make sure your Withings device has synced recently."

    # ctx already has "Health data:" as header — reformat for Telegram
    lines = ["<b>❤️ Health</b>"]
    for line in ctx.splitlines():
        stripped = line.strip()
        if stripped and stripped != "Health data:":
            lines.append(stripped.replace("  - ", "• ").replace("- ", "• "))
    return "\n".join(lines)


def _reply_streaks(tz_offset: int) -> str:
    """Show current streak for each active habit."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today_str = now_local.date().isoformat()

    with SessionLocal() as db:
        habits = db.query(models.Habit).filter_by(archived=False).order_by(models.Habit.id).all()
        if not habits:
            return "You don't have any habits set up yet."

        # Most recent HabitStreakDay per habit
        streaks: dict[int, int] = {}
        for h in habits:
            row = (
                db.query(models.HabitStreakDay)
                .filter_by(habit_id=h.id)
                .order_by(models.HabitStreakDay.date.desc())
                .first()
            )
            streaks[h.id] = row.streak if row else 0

        # Today's completions to show ✓/○ status
        completed_ids = {
            r.habit_id for r in db.query(models.HabitCompletion).filter_by(date=today_str).all()
        }

    if not any(streaks.values()):
        return "No streaks yet — keep completing your habits every day to build one!"

    lines = ["<b>🔥 Habit streaks</b>\n"]
    for h in habits:
        s = streaks.get(h.id, 0)
        check = "✓" if h.id in completed_ids else "○"
        flame = " 🔥" if s >= 3 else ""
        streak_txt = f"{s} day{'s' if s != 1 else ''}" if s > 0 else "no streak"
        lines.append(f"{check} {h.name} — {streak_txt}{flame}")
    return "\n".join(lines)


def _reply_priority(tz_offset: int) -> str:
    """Use the LLM to suggest what to focus on next."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    with SessionLocal() as db:
        today_cards = (
            db.query(models.Card)
            .filter_by(section="today", completed=False, archived=False)
            .order_by(models.Card.position)
            .all()
        )
        overdue = [
            c for c in today_cards
            if c.scheduled_at and c.scheduled_at.date() < today
        ]

    cal_events = _fetch_cal_events_for_date(today, tz_offset)
    upcoming_events = [
        ev for ev in cal_events
        if not ev.all_day and ev.start.replace(tzinfo=None) - timedelta(minutes=tz_offset) >= now_local
    ]

    if not today_cards and not upcoming_events:
        return "Nothing on your plate right now — enjoy the clear schedule!"

    # Build context for LLM
    ctx_lines = [f"Current time: {now_local.strftime('%-I:%M %p')} on {today.strftime('%A, %B %-d')}"]
    if overdue:
        ctx_lines.append(f"Overdue tasks ({len(overdue)}):")
        for c in overdue:
            days = (today - c.scheduled_at.date()).days
            ctx_lines.append(f"  - {c.title} ({days}d overdue)")
    if upcoming_events:
        ctx_lines.append("Upcoming calendar events:")
        for ev in upcoming_events[:3]:
            ctx_lines.append(f"  - {ev.title} at {_fmt_event_time(ev, tz_offset)}")
    pending = [c for c in today_cards if c not in overdue]
    if pending:
        ctx_lines.append("Other tasks today:")
        for c in pending[:8]:
            ctx_lines.append(f"  - {c.title}")

    system = (
        "You are a personal productivity assistant. Based on the user's current tasks "
        "and schedule, suggest the single most important thing to focus on right now. "
        "Reply with 1-2 sentences max — be direct and specific. No lists, no bullet points."
    )
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(ctx_lines)},
            ],
            timeout=15,
            temperature=0.3,
        )
        suggestion = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[telegram] priority LLM error: {e}")
        if overdue:
            return f"🎯 Start with your most overdue task: <b>{overdue[0].title}</b>"
        if upcoming_events:
            return f"🎯 You have <b>{upcoming_events[0].title}</b> coming up — prepare for that."
        return f"🎯 Start with: <b>{today_cards[0].title}</b>"

    return f"🎯 {suggestion}"


def _reply_undo(chat_id: str) -> str:
    """Reverse the most recent capture or mark_complete for this chat."""
    action = _last_actions.pop(chat_id, None)
    if not action:
        return "Nothing to undo."

    card_id = action["card_id"]
    title   = action["title"]

    with SessionLocal() as db:
        card = db.query(models.Card).filter_by(id=card_id).first()
        if not card:
            return f'Could not undo — "{title}" no longer exists.'

        if action["type"] == "capture":
            db.delete(card)
            db.commit()
            return f"↩ Removed: <b>{title}</b>"

        if action["type"] == "mark_complete":
            if not card.completed:
                return f'"{title}" is already not marked complete.'
            card.completed    = False
            card.completed_at = None
            db.commit()
            return f"↩ Unmarked complete: <b>{title}</b>"

    return "Nothing to undo."
