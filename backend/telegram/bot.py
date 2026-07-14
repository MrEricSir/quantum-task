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
{habits_section}
{last_card_section}

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

  "complete_habit"
      User is marking a habit done for today. Use this when the thing being completed
      matches one of the available habits, not a task.
      Also return "match_query": the habit name or fragment to match.
      Examples: "done meditation", "finished yoga", "did my workout", "mark reading complete",
                "exercise done", "I meditated"

  "query_avoiding"
      User wants to know what they've been putting off or avoiding.
      Examples: "what am I avoiding?", "what have I been putting off?",
                "what keeps getting pushed?", "what am I procrastinating on?"

  "reschedule"
      User wants to move or reschedule an existing task to a different date, time, or section.
      Also return:
        "match_query"  — task title or fragment to find (required)
        "date"         — resolved target date as YYYY-MM-DD, or null if only a section change
        "time"         — target time as HH:MM (24h), or null if no specific time given
        "section"      — "today" | "week" | "month" | "later", or null to infer from date
      For relative phrases: "next week" → section="week" + date=Monday of next week,
      "push to next month" → section="month", "move to today" → date=today + section="today".
      Examples: "move dentist to Thursday at 2pm", "push the report to next week",
                "reschedule groceries to tomorrow", "move call to next Monday at 10am",
                "push everything to next week", "delay the report by 3 days"

Reply ONLY with valid JSON. No explanation.\
"""


# ── Per-chat session state (in-memory) ───────────────────────────────────────
# Keyed by chat_id. Each value is a dict with:
#   "undo"    — last reversible action (or None)
#   "last_card" — {"id": int, "title": str} of the most recently mentioned card
#   "pending" — pending disambiguation: {"action": str, "intent": dict, "candidates": [...]}
_sessions: dict[str, dict] = {}


def _get_session(chat_id: str) -> dict:
    if chat_id not in _sessions:
        _sessions[chat_id] = {"undo": None, "last_card": None, "pending": None}
    return _sessions[chat_id]


# Words/phrases treated as pronoun references to the last mentioned card
_PRONOUN_REFS = {"it", "that", "this", "the task", "that one", "this one",
                 "the one", "that task", "this task"}


# ── LLM intent classification ─────────────────────────────────────────────────

def _parse_telegram_intent(text: str, tz_offset: int, chat_id: str = "") -> dict:
    """Call the LLM with the Telegram intent prompt and return parsed JSON."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()
    tomorrow = today + timedelta(days=1)

    with SessionLocal() as db:
        tag_names   = [t.name for t in db.query(models.Tag).order_by(models.Tag.name).all()]
        habit_names = [h.name for h in db.query(models.Habit).filter_by(archived=False).order_by(models.Habit.id).all()]

    tags_section   = f"Available tags: {', '.join(tag_names)}"   if tag_names   else "No tags defined."
    habits_section = f"Available habits: {', '.join(habit_names)}" if habit_names else "No habits defined."

    last_card_section = ""
    if chat_id:
        session = _get_session(chat_id)
        if session.get("last_card"):
            lc = session["last_card"]
            last_card_section = (
                f'Last mentioned task: "{lc["title"]}" — '
                f'if the user says "it", "that", "this task", or similar, resolve to this task.'
            )

    prompt = _TELEGRAM_INTENT_PROMPT.format(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        tomorrow=tomorrow.isoformat(),
        tags_section=tags_section,
        habits_section=habits_section,
        last_card_section=last_card_section,
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

    # Check for pending disambiguation first
    if chat_id:
        result = _resolve_disambiguation(_get_session(chat_id), lower, tz_offset, chat_id)
        if result is not None:
            return result

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
            "<b>done [habit]</b> — mark a habit complete (e.g. done meditation)\n"
            "<b>move [task] to [date]</b> — reschedule a task\n"
            "<b>avoiding</b> — see what you've been putting off\n"
            "<b>undo</b> — reverse your last capture, completion, or reschedule\n"
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
    if lower in ("avoiding", "procrastinating", "putting off"):
        return _reply_avoiding(tz_offset)

    # LLM intent classification for everything else
    print(f"[telegram] classifying intent: {text[:60]!r}")
    try:
        intent = _parse_telegram_intent(text, tz_offset, chat_id)
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

    if action == "reschedule":
        return _reply_reschedule(intent, tz_offset, chat_id)

    if action == "complete_habit":
        query = intent.get("match_query") or ""
        if not query:
            return "Which habit did you complete? Try: <b>done meditation</b>"
        return _reply_complete_habit(query, chat_id)

    if action == "query_avoiding":
        return _reply_avoiding(tz_offset)

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


def _fuzzy_find_cards(cards: list, query: str) -> list:
    """Return best-matching cards (up to 3) for disambiguation.

    Strategy: exact → substring (either direction) → word overlap.
    A single exact or unambiguous substring match returns a one-element list.
    Multiple equally-good matches return up to 3 for the caller to disambiguate.
    """
    q = query.lower().strip()
    q_words = {w for w in q.split() if len(w) > 2}

    # 1. Exact match — always unambiguous
    for c in cards:
        if c.title.lower() == q:
            return [c]

    # 2. Substring: query in title, or title in query
    sub = [c for c in cards if q in c.title.lower() or c.title.lower() in q]
    if sub:
        return sub[:3]

    # 3. Word overlap — most shared words wins; ties kept for disambiguation
    if q_words:
        scored = [(len(q_words & set(c.title.lower().split())), c) for c in cards]
        scored = [(s, c) for s, c in scored if s > 0]
        if scored:
            scored.sort(key=lambda x: -x[0])
            top = scored[0][0]
            return [c for s, c in scored if s == top][:3]

    return []


def _resolve_disambiguation(session: dict, text: str, tz_offset: int, chat_id: str):
    """If a disambiguation is pending, try to resolve the user's selection.

    Returns a reply string if resolved or cancelled, None if text is unrelated.
    """
    pending = session.get("pending")
    if not pending:
        return None

    candidates = pending["candidates"]  # list of {"id": int, "title": str}
    lower = text.strip().lower()
    selected = None

    # Numeric selection: "1", "2", "3"
    if lower in {str(i + 1) for i in range(len(candidates))}:
        selected = candidates[int(lower) - 1]

    # Title substring match
    if not selected:
        for c in candidates:
            if lower in c["title"].lower() or c["title"].lower() in lower:
                selected = c
                break

    if not selected:
        # Unrelated message — clear pending and fall through to normal routing
        session["pending"] = None
        return None

    session["pending"] = None

    action = pending["action"]

    if action == "complete":
        with SessionLocal() as db:
            card = db.query(models.Card).filter_by(id=selected["id"]).first()
            if not card or card.completed:
                return f'"{selected["title"]}" is already done or not found.'
            card.completed = True
            card.completed_at = datetime.now(timezone.utc)
            db.commit()
        session["undo"] = {"type": "mark_complete", "card_id": selected["id"], "title": selected["title"]}
        session["last_card"] = {"id": selected["id"], "title": selected["title"]}
        return f'✓ Marked complete: <b>{selected["title"]}</b>\nSend <b>undo</b> to reverse.'

    if action == "reschedule":
        intent = dict(pending["intent"])
        intent["match_query"] = selected["title"]  # exact title → unambiguous re-run
        return _reply_reschedule(intent, tz_offset, chat_id)

    return None


def _reply_complete(query: str, chat_id: str = "") -> str:
    session = _get_session(chat_id) if chat_id else {}

    # Resolve pronoun references to last mentioned card
    if query.lower().strip() in _PRONOUN_REFS and session.get("last_card"):
        query = session["last_card"]["title"]

    with SessionLocal() as db:
        cards = db.query(models.Card).filter_by(completed=False, archived=False).all()
        matches = _fuzzy_find_cards(cards, query)

        if not matches:
            return f'Couldn\'t find a task matching "{query}". Try <b>today</b> to see your list.'

        if len(matches) > 1:
            if chat_id:
                session["pending"] = {
                    "action": "complete",
                    "intent": {},
                    "candidates": [{"id": c.id, "title": c.title} for c in matches],
                }
            numbered = "\n".join(f"{i + 1}. {c.title}" for i, c in enumerate(matches))
            return f"Which task did you mean?\n{numbered}"

        match = matches[0]
        match.completed = True
        match.completed_at = datetime.now(timezone.utc)
        card_id = match.id
        title = match.title
        db.commit()

    if chat_id:
        session["undo"] = {"type": "mark_complete", "card_id": card_id, "title": title}
        session["last_card"] = {"id": card_id, "title": title}

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
        session = _get_session(chat_id)
        session["undo"] = {"type": "capture", "card_id": card_id, "title": title}
        session["last_card"] = {"id": card_id, "title": title}

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
        session = _get_session(chat_id)
        session["undo"] = {"type": "capture", "card_id": card_id, "title": text}
        session["last_card"] = {"id": card_id, "title": text}

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


def _reply_complete_habit(query: str, chat_id: str = "") -> str:
    """Mark a habit complete for today."""
    from streak import recompute_from
    now_local = datetime.now(timezone.utc).replace(tzinfo=None)  # rough — used only for date
    with SessionLocal() as db:
        s = Settings(db)
        tz_offset = s.tz_offset
        today = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)).date()
        today_str = today.isoformat()

        habits = db.query(models.Habit).filter_by(archived=False).all()
        q = query.lower().strip()

        # Exact match first, then substring, then word overlap
        match = None
        for h in habits:
            if h.name.lower() == q:
                match = h
                break
        if not match:
            for h in habits:
                if q in h.name.lower() or h.name.lower() in q:
                    match = h
                    break
        if not match:
            q_words = {w for w in q.split() if len(w) > 2}
            if q_words:
                best, best_score = None, 0
                for h in habits:
                    score = len(q_words & set(h.name.lower().split()))
                    if score > best_score:
                        best, best_score = h, score
                if best_score > 0:
                    match = best

        if not match:
            names = ", ".join(h.name for h in habits) if habits else "none"
            return f'No habit matching "{query}". Your habits: {names}'

        # Check if already done today
        existing = db.query(models.HabitCompletion).filter_by(habit_id=match.id, date=today_str).first()
        if existing:
            return f"✓ <b>{match.name}</b> was already marked done today."

        db.add(models.HabitCompletion(habit_id=match.id, date=today_str))
        db.flush()
        recompute_from(db, match.id, today)
        db.commit()

        # Get current streak for a little feedback
        from streak import get_current_streak
        streak = get_current_streak(db, match.id, today)

    streak_note = f" ({streak}-day streak 🔥)" if streak >= 2 else ""
    return f"✓ <b>{match.name}</b> done for today{streak_note}"


def _reply_avoiding(tz_offset: int) -> str:
    """LLM-powered analysis of stale/repeatedly-pushed tasks."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()
    cutoff_date = today - timedelta(days=3)
    stale_created = today - timedelta(days=7)

    with SessionLocal() as db:
        candidates = db.query(models.Card).filter(
            models.Card.completed == False,   # noqa: E712
            models.Card.archived == False,    # noqa: E712
            models.Card.section.in_(["today", "week"]),
        ).all()

    # Overdue by >3 days, or sitting in today/week for >7 days without a scheduled date
    stuck = []
    for c in candidates:
        if c.scheduled_at and c.scheduled_at.date() <= cutoff_date:
            days_overdue = (today - c.scheduled_at.date()).days
            stuck.append((c.title, f"{days_overdue}d overdue"))
        elif not c.scheduled_at and c.created_at:
            created_local = (c.created_at.replace(tzinfo=None) - timedelta(minutes=tz_offset)).date()
            if created_local <= stale_created:
                days_old = (today - created_local).days
                stuck.append((c.title, f"added {days_old}d ago, never scheduled"))

    if not stuck:
        return "Nothing looks stuck right now — you're on top of things!"

    task_lines = "\n".join(f"- {title} ({reason})" for title, reason in stuck)
    system = (
        "You are a direct, empathetic productivity coach. The user has these tasks "
        "that appear to be stuck or repeatedly avoided. Call them out honestly — "
        "name the tasks specifically, speculate briefly on why they might be stuck "
        "(too vague? too big? dreading it?), and end with one practical suggestion. "
        "2-4 sentences. No bullet points."
    )
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"My stuck tasks:\n{task_lines}"},
            ],
            timeout=15,
            temperature=0.4,
        )
        insight = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[telegram] avoiding LLM error: {e}")
        lines = [f"<b>🪨 {len(stuck)} stuck task{'s' if len(stuck) != 1 else ''}</b>\n"]
        for title, reason in stuck:
            lines.append(f"• {title} ({reason})")
        return "\n".join(lines)

    header = f"<b>🪨 {len(stuck)} task{'s' if len(stuck) != 1 else ''} that keep getting pushed</b>\n"
    return header + insight


def _section_for_date(d, today) -> str:
    delta = (d - today).days
    if delta <= 0:
        return "today"
    if delta <= 7:
        return "week"
    if delta <= 30:
        return "month"
    return "later"


def _reply_reschedule(intent: dict, tz_offset: int, chat_id: str = "") -> str:
    """Find a card by match_query and update its date/section."""
    from datetime import date as _date

    query = (intent.get("match_query") or "").strip().lower()
    if not query:
        return "What task should I reschedule? Try: <b>move dentist to Thursday at 2pm</b>"

    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    # Parse target date
    date_str = intent.get("date")
    target_date: _date | None = None
    if date_str:
        try:
            target_date = _date.fromisoformat(date_str)
        except (ValueError, TypeError):
            pass

    # Parse target time
    time_str = intent.get("time")
    target_time = None
    if time_str:
        try:
            from datetime import time as _time
            parts = time_str.split(":")
            target_time = _time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except (ValueError, TypeError, IndexError):
            pass

    # Determine section
    section = intent.get("section")
    if section not in ("today", "week", "month", "later"):
        section = _section_for_date(target_date, today) if target_date else None

    if not target_date and not section:
        return "I couldn't figure out the new date. Try: <b>move dentist to Thursday</b>"

    # Build scheduled_at
    scheduled_at = None
    if target_date and target_time:
        scheduled_at = datetime.combine(target_date, target_time)
    elif target_date and not target_time:
        scheduled_at = None  # date-only move — clear the time

    if not section and target_date:
        section = _section_for_date(target_date, today)

    # Resolve pronoun references
    session = _get_session(chat_id) if chat_id else {}
    if query in _PRONOUN_REFS and session.get("last_card"):
        query = session["last_card"]["title"]

    # Find card by fuzzy match
    with SessionLocal() as db:
        all_cards = db.query(models.Card).filter_by(completed=False, archived=False).all()
        matches = _fuzzy_find_cards(all_cards, query)

        if not matches:
            return f'Couldn\'t find a task matching "{intent.get("match_query")}". Try <b>today</b> to see your list.'

        if len(matches) > 1:
            if chat_id:
                session["pending"] = {
                    "action": "reschedule",
                    "intent": intent,
                    "candidates": [{"id": c.id, "title": c.title} for c in matches],
                }
            numbered = "\n".join(f"{i + 1}. {c.title}" for i, c in enumerate(matches))
            return f"Which task did you mean?\n{numbered}"

        match = matches[0]
        old_section = match.section
        old_scheduled_at = match.scheduled_at
        card_id = match.id
        title = match.title

        if section:
            match.section = section
        if target_date:
            match.scheduled_at = scheduled_at  # may be None if no time given
        db.commit()

    if chat_id:
        session["undo"] = {
            "type": "reschedule",
            "card_id": card_id,
            "title": title,
            "old_section": old_section,
            "old_scheduled_at": old_scheduled_at,
        }
        session["last_card"] = {"id": card_id, "title": title}

    # Build confirmation label
    if target_date:
        if target_time:
            date_label = f"{target_date.strftime('%A, %b %-d')} at {target_time.strftime('%-I:%M %p')}"
        else:
            date_label = target_date.strftime('%A, %b %-d')
    else:
        date_label = {"today": "Today", "week": "This Week", "month": "This Month", "later": "Later"}.get(section, section)

    return f"↗ Moved <b>{title}</b> to {date_label}\nSend <b>undo</b> to reverse."


def _reply_undo(chat_id: str) -> str:
    """Reverse the most recent reversible action for this chat."""
    session = _get_session(chat_id)
    action = session.get("undo")
    if not action:
        return "Nothing to undo."
    session["undo"] = None

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

        if action["type"] == "reschedule":
            card.section      = action["old_section"]
            card.scheduled_at = action["old_scheduled_at"]
            db.commit()
            return f"↩ Moved <b>{title}</b> back to {action['old_section'].title()}"

    return "Nothing to undo."
