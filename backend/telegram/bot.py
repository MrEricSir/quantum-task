"""
Telegram bot message routing and reply generation.

Entry point: handle_update(update_dict) — call this from the webhook endpoint.
All DB sessions are opened/closed inside each function; no session is passed in.
"""
from __future__ import annotations

import json as _json
import traceback
from datetime import datetime, timezone, timedelta

import app_setting_keys as keys
from capabilities.food import parse_food_entries
from routers.cards import update_card_row
from capabilities.workout import parse_workout
from capabilities.registry import REGISTRY, by_telegram_action, set_telegram_handler
import models
from database import SessionLocal
from deps import llm_client, LLM_MODEL
from gcal import get_personal_events
from settings import Settings
from telegram.notify import send_message


# ── Intent prompt ─────────────────────────────────────────────────────────────
# Capability action blocks (mark_complete, complete_habit, log_food, log_mood)
# come from the capabilities registry so they stay in sync with the parse-flow prompt.

_TELEGRAM_INTENT_PROMPT = (
"""\
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

  "query_completed"
      User wants to see what tasks they've finished, usually today.
      Also return "date": resolved date as YYYY-MM-DD. Default to today.
      Examples: "what did I complete today?", "what did I get done?", "show me my wins",
                "what have I accomplished?", "completed tasks", "what did I finish today?"

  "search_cards"
      User wants to find tasks or notes by topic, concept, or keyword — not by exact title.
      Also return:
        "query" — the topic or phrase to search for (required)
      Examples: "what did I write about the deployment?", "find cards about billing",
                "do I have anything about the API?", "show me everything about marketing",
                "what notes do I have on the sprint?"

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

  "add_note"
      User wants to add a note or extra detail to an existing task.
      Also return:
        "match_query" — task title or fragment to find (required)
        "note"        — text to append to the task's notes (required)
      Examples: "add a note to dentist: bring insurance card",
                "note on grocery run: also get olive oil",
                "append to the API task: check rate limits first"

  "read_note"
      User wants to see the description or notes stored on an existing task.
      Also return:
        "match_query" — task title or fragment to find (required)
      Examples: "what's the note on dentist?", "notes on the API task",
                "what did I write about groceries?", "details on the dentist card"

"""
+ REGISTRY["task_complete"].telegram_description
+ """

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

  "query_weather"
      User wants the current weather or forecast.
      Examples: "what's the weather like today?", "is it going to rain?",
                "how cold is it out?", "do I need a jacket?", "weather forecast"

  "query_streaks"
      User wants habit streak information.
      Examples: "what are my streaks?", "how long is my meditation streak?",
                "how many days in a row?", "habit streaks"

  "query_priority"
      User wants a recommendation on what to work on or focus on next.
      Examples: "what should I work on?", "what's my priority?", "what should I do next?",
                "help me focus", "what's most important right now?"

  "ask_schedule"
      User is asking a question that requires REASONING over today's schedule --
      finding a specific item, computing a gap/duration, or answering a yes/no
      about availability -- rather than wanting the whole day listed out
      (use "query_schedule" for that instead).
      Also return:
        "question" — the user's question, verbatim
        "duration_minutes" — if the question asks whether there's time for something,
            the duration in minutes (stated or clearly implied, e.g. "a quick nap" → 15,
            "a workout" → 45, "a 20 minute nap" → 20). null if no availability/fit
            question is being asked, or a duration truly can't be estimated.
      Examples: "what's the last thing I have scheduled today?", "do I have time for a
                quick nap?" (duration_minutes=15), "am I free before 3pm?", "how much of
                a gap do I have this afternoon?", "what's my first meeting?",
                "is there room for a 30 minute workout today?" (duration_minutes=30)

"""
+ REGISTRY["habit_check"].telegram_description
+ """

  "query_avoiding"
      User wants to know what they've been putting off or avoiding.
      Examples: "what am I avoiding?", "what have I been putting off?",
                "what keeps getting pushed?", "what am I procrastinating on?"

"""
+ REGISTRY["food"].telegram_description
+ """

"""
+ REGISTRY["mood"].telegram_description
+ """

"""
+ REGISTRY["workout"].telegram_description
+ """

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

  "bulk_reschedule"
      User wants to move MULTIPLE tasks at once (a group, not a single named task).
      Also return:
        "filter"  — which tasks to target: "overdue" | "today" | "week" | "all_incomplete"
        "section" — target section: "today" | "week" | "month" | "later"
        "date"    — resolved target date as YYYY-MM-DD, or null
      Use "overdue" when user says "overdue" or "late". Use "today" for "today's list" / "today's tasks".
      Examples: "move everything overdue to next week", "push all overdue to next month",
                "clear today's list" (filter=today, section=later),
                "move today's tasks to tomorrow" (filter=today, section=today, date=tomorrow)

  "queue_bridge"
      User wants to queue a Claude Code build job for a specific task/card.
      Also return:
        "match_query" — task title or fragment to find (required)
      Examples: "/build auth feature", "build the login card", "run claude on the API task",
                "/build 42", "queue build for the dashboard feature"

Reply ONLY with valid JSON. No explanation.\
"""
)


# ── Per-chat session state (in-memory) ───────────────────────────────────────
# Keyed by chat_id. Each value is a dict with:
#   "undo"    — last reversible action (or None)
#   "last_card" — {"id": int, "title": str} of the most recently mentioned card
#   "pending" — pending disambiguation: {"action": str, "intent": dict, "candidates": [...]}
_sessions: dict[str, dict] = {}


def _get_session(chat_id: str) -> dict:
    if chat_id not in _sessions:
        _sessions[chat_id] = {"undo": None, "undo_prev": None, "last_card": None, "pending": None, "last_location": None}
    return _sessions[chat_id]


def _push_undo(session: dict, action: dict) -> None:
    """Push a new undo action, keeping the previous one for 'undo both'."""
    session["undo_prev"] = session.get("undo")
    session["undo"] = action


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
        chat_id_incoming = str(msg.get("chat", {}).get("id", ""))
        update_id = update.get("update_id")

        with SessionLocal() as db:
            s = Settings(db)
            token     = s.telegram_token
            chat_id   = s.telegram_chat_id
            tz_offset = s.tz_offset

            # Telegram resends an update if our webhook response doesn't arrive in
            # time (e.g. a slow LLM call during a Cloud Run cold start). Without
            # this, a slow-but-successful reply still gets reprocessed -- and
            # re-sent -- on every retry, which is what produced repeated duplicate
            # replies (e.g. logging the same food item several times over).
            if update_id is not None:
                last_update_id = s.telegram_last_update_id
                if update_id <= last_update_id:
                    print(f"[telegram] duplicate update_id={update_id} (last processed={last_update_id}), skipping")
                    return
                s.set(keys.TELEGRAM_LAST_UPDATE_ID, str(update_id))
                db.commit()

        print(f"[telegram] incoming chat_id={chat_id_incoming!r} configured={chat_id!r} token_set={bool(token)}")

        if not token or not chat_id:
            print("[telegram] bot not configured, dropping update")
            return
        if chat_id_incoming != chat_id:
            print(f"[telegram] chat_id mismatch: got {chat_id_incoming!r} expected {chat_id!r}")
            return

        # A shared-location message has no "text" field at all -- handle it
        # before the text checks below, so it isn't silently dropped. Stashed
        # in the per-chat session for _reply_weather to prefer over the
        # webapp's LAST_KNOWN_LAT/LON fallback when both are available.
        location = msg.get("location")
        if location and location.get("latitude") is not None and location.get("longitude") is not None:
            session = _get_session(chat_id)
            session["last_location"] = {
                "lat": location["latitude"],
                "lon": location["longitude"],
                "at": datetime.now(timezone.utc),
            }
            ok = send_message(token, chat_id, "📍 Got it — I'll use that for weather.")
            print(f"[telegram] location stored, send_message ok={ok}")
            return

        text = (msg.get("text") or "").strip()
        if not text:
            print("[telegram] update has no text, skipping")
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
            "<b>weather</b> — current conditions and forecast\n"
            "<b>overdue</b> — overdue tasks\n"
            "<b>completed</b> — what you finished today\n"
            "<b>priority</b> — what to focus on next\n"
            "<b>done [task]</b> — mark a task complete\n"
            "<b>done [habit]</b> — mark a habit complete (e.g. done meditation)\n"
            "<b>note on [task]: [text]</b> — add a note to a task\n"
            "<b>notes on [task]</b> — read a task's notes\n"
            "<b>move [task] to [date]</b> — reschedule a task\n"
            "<b>move all overdue to next week</b> — bulk reschedule\n"
            "<b>avoiding</b> — see what you've been putting off\n"
            "<b>undo</b> — reverse your last action\n"
            "<b>/build [task]</b> — queue a Claude Code build job for a card\n"
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
    if lower in ("undo both", "undo all", "undo all of that", "undo everything"):
        return _reply_undo(chat_id, count=2)
    if lower in ("week", "this week", "week ahead"):
        return _reply_week(tz_offset)
    if lower in ("health", "steps", "fitness"):
        return _reply_health(tz_offset)
    if lower in ("weather", "forecast"):
        return _reply_weather(chat_id)
    if lower in ("streaks", "streak"):
        return _reply_streaks(tz_offset)
    if lower in ("priority", "focus", "next"):
        return _reply_priority(tz_offset)
    if lower in ("avoiding", "procrastinating", "putting off"):
        return _reply_avoiding(tz_offset)
    if lower in ("completed", "done today", "wins", "accomplished", "finished"):
        return _reply_completed(tz_offset)
    if lower.startswith("/build ") or lower.startswith("build "):
        query = text.split(" ", 1)[1].strip() if " " in text else ""
        if query:
            return _reply_queue_bridge({"match_query": query}, chat_id)

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

    if action == "query_weather":
        return _reply_weather(chat_id)

    if action == "query_streaks":
        return _reply_streaks(tz_offset)

    if action == "query_priority":
        return _reply_priority(tz_offset)

    if action == "ask_schedule":
        return _reply_ask_schedule(intent, tz_offset)

    if action == "reschedule":
        return _reply_reschedule(intent, tz_offset, chat_id)

    op = by_telegram_action(action)
    if op and op.telegram_handler:
        return op.telegram_handler(intent, tz_offset, chat_id)

    if action == "query_avoiding":
        return _reply_avoiding(tz_offset)

    if action == "query_completed":
        return _reply_completed(tz_offset, intent.get("date"))

    if action == "search_cards":
        return _reply_search_cards(intent, chat_id)

    if action == "add_note":
        return _reply_add_note(intent, chat_id)

    if action == "read_note":
        return _reply_read_note(intent, chat_id)

    if action == "bulk_reschedule":
        return _reply_bulk_reschedule(intent, tz_offset, chat_id)

    if action == "queue_bridge":
        return _reply_queue_bridge(intent, chat_id)

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

    # "capture" or anything unrecognised
    return _capture_from_text(text, tz_offset, chat_id)


# ── Calendar helpers ──────────────────────────────────────────────────────────

def _fetch_cal_events_for_date(target_date, tz_offset: int) -> list[dict]:
    """Fetch calendar events for a specific date using the shared calendar helper."""
    try:
        with SessionLocal() as db:
            return get_personal_events(db, target_date, target_date + timedelta(days=1), tz_offset)
    except Exception as e:
        print(f"[telegram] calendar fetch error: {e}")
        return []


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
        for ev in sorted(cal_events, key=lambda e: (e["all_day"], e["start"])):
            lines.append(f"• {ev['title']} @ {ev['time_str']}")

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
        for ev in sorted(cal_events, key=lambda e: (e["all_day"], e["start"])):
            lines.append(f"• {ev['title']} @ {ev['time_str']}")

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


def _reply_completed(tz_offset: int, date_str: str | None = None) -> str:
    """Show tasks completed today (or on a specific date)."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    if date_str:
        try:
            from datetime import date as _date
            target = _date.fromisoformat(date_str)
        except (ValueError, TypeError):
            target = now_local.date()
    else:
        target = now_local.date()

    with SessionLocal() as db:
        completed = (
            db.query(models.Card)
            .filter(
                models.Card.completed == True,  # noqa: E712
                models.Card.completed_at.isnot(None),
            )
            .order_by(models.Card.completed_at.desc())
            .all()
        )

    # Filter to local date
    day_done = [
        c for c in completed
        if (c.completed_at.replace(tzinfo=None) - timedelta(minutes=tz_offset)).date() == target
    ]

    is_today = target == now_local.date()
    label = "today" if is_today else target.strftime("%A, %b %-d")

    if not day_done:
        return f"Nothing completed {label} yet." + (" You've got this!" if is_today else "")

    n = len(day_done)
    lines = [f"<b>✓ {n} task{'s' if n != 1 else ''} completed {label}</b>\n"]
    for c in day_done:
        lines.append(f"✓ {c.title}")
    if is_today:
        lines.append("\nNice work! 💪")
    return "\n".join(lines)


def _reply_add_note(intent: dict, chat_id: str = "") -> str:
    """Append a note to an existing card's description."""
    query = (intent.get("match_query") or "").strip()
    note  = (intent.get("note") or "").strip()

    if not query:
        return "Which task? Try: <b>add a note to dentist: bring insurance card</b>"
    if not note:
        return "What's the note? Try: <b>add a note to dentist: bring insurance card</b>"

    session = _get_session(chat_id) if chat_id else {}

    if query.lower() in _PRONOUN_REFS and session.get("last_card"):
        query = session["last_card"]["title"]

    with SessionLocal() as db:
        cards = db.query(models.Card).filter_by(archived=False).all()
        matches = _fuzzy_find_cards(cards, query)

        if not matches:
            return f'Couldn\'t find a task matching "{query}". Try <b>today</b> to see your list.'

        if len(matches) > 1:
            if chat_id:
                session["pending"] = {
                    "action": "add_note",
                    "intent": intent,
                    "candidates": [{"id": c.id, "title": c.title} for c in matches],
                }
            numbered = "\n".join(f"{i + 1}. {c.title}" for i, c in enumerate(matches))
            return f"Which task?\n{numbered}"

        match = matches[0]
        old_description = match.description or ""
        new_description = (old_description + "\n" + note).strip() if old_description else note
        title = match.title
        # "completed" is deliberately absent from this data dict -- adding a
        # note must never trigger update_card_row's recurrence-spawn logic.
        match = update_card_row(db, match, {"description": new_description}, None, datetime.now(timezone.utc).date())
        card_id = match.id

    if chat_id:
        _push_undo(session, {"type": "add_note", "card_id": card_id, "title": title, "old_description": old_description})
        session["last_card"] = {"id": card_id, "title": title}

    return f'📝 Note added to <b>{title}</b>\nSend <b>undo</b> to remove it.'


def _reply_read_note(intent: dict, chat_id: str = "") -> str:
    """Return the description/notes stored on a card."""
    query = (intent.get("match_query") or "").strip()

    if not query:
        return "Which task? Try: <b>notes on dentist</b>"

    session = _get_session(chat_id) if chat_id else {}

    if query.lower() in _PRONOUN_REFS and session.get("last_card"):
        query = session["last_card"]["title"]

    with SessionLocal() as db:
        cards = db.query(models.Card).filter_by(archived=False).all()
        matches = _fuzzy_find_cards(cards, query)

        if not matches:
            return f'Couldn\'t find a task matching "{query}".'

        if len(matches) > 1:
            if chat_id:
                session["pending"] = {
                    "action": "read_note",
                    "intent": intent,
                    "candidates": [{"id": c.id, "title": c.title} for c in matches],
                }
            numbered = "\n".join(f"{i + 1}. {c.title}" for i, c in enumerate(matches))
            return f"Which task?\n{numbered}"

        match = matches[0]
        title = match.title
        description = match.description
        card_id = match.id

    if chat_id:
        session["last_card"] = {"id": card_id, "title": title}

    if not description:
        return f'<b>{title}</b> has no notes.'
    return f'<b>{title}</b>\n\n{description}'


def _reply_search_cards(intent: dict, chat_id: str = "") -> str:
    """Semantic search across cards and GitHub engineering items."""
    query = (intent.get("query") or "").strip()
    if not query:
        return "What would you like to search for?"

    import embeddings as _embeddings
    lines = []

    with SessionLocal() as db:
        # --- Cards ---
        card_ids = _embeddings.search(db, query, top_k=5)
        if card_ids:
            id_order = {cid: i for i, cid in enumerate(card_ids)}
            cards = db.query(models.Card).filter(
                models.Card.archived == False,  # noqa: E712
                models.Card.id.in_(card_ids),
            ).all()
            cards.sort(key=lambda c: id_order.get(c.id, 999))
        else:
            q = query.lower()
            cards = db.query(models.Card).filter(
                models.Card.archived == False,  # noqa: E712
                (models.Card.title.ilike(f"%{q}%") | models.Card.description.ilike(f"%{q}%")),
            ).limit(5).all()

        if cards:
            lines.append("<b>Tasks / Notes</b>")
            for c in cards:
                status = "✅" if c.completed else ("📌" if c.section == "none" else "⬜")
                lines.append(f"{status} <b>{c.title}</b>")
                if c.description:
                    snippet = c.description[:80].replace("\n", " ")
                    if len(c.description) > 80:
                        snippet += "…"
                    lines.append(f"   <i>{snippet}</i>")
            if chat_id:
                _get_session(chat_id)["last_card"] = {"id": cards[0].id, "title": cards[0].title}

        # --- Engineering items ---
        eng_ids = _embeddings.search_eng(db, query, top_k=3)
        if eng_ids:
            id_order = {iid: i for i, iid in enumerate(eng_ids)}
            eng_items = db.query(models.EngineeringItem).filter(
                models.EngineeringItem.id.in_(eng_ids),
            ).all()
            eng_items.sort(key=lambda e: id_order.get(e.id, 999))
        else:
            q = query.lower()
            eng_items = db.query(models.EngineeringItem).filter(
                models.EngineeringItem.title.ilike(f"%{q}%"),
            ).limit(3).all()

        if eng_items:
            if lines:
                lines.append("")
            lines.append("<b>GitHub</b>")
            for e in eng_items:
                icon = "🔴" if e.state == "closed" else "🟢"
                status_tag = f" [{e.project_status}]" if e.project_status else ""
                lines.append(f"{icon} <b>{e.title}</b>{status_tag}")
                lines.append(f"   <i>{e.repo}</i>")

    if not lines:
        return f'No results found matching "{query}".'

    return f'Search results for "<b>{query}</b>":\n\n' + "\n".join(lines)


def _reply_bulk_reschedule(intent: dict, tz_offset: int, chat_id: str = "") -> str:
    """Move a group of tasks at once based on a filter."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    filter_type = (intent.get("filter") or "overdue").lower()
    section = intent.get("section")
    date_str = intent.get("date")

    target_date = None
    if date_str:
        try:
            from datetime import date as _date
            target_date = _date.fromisoformat(date_str)
        except (ValueError, TypeError):
            pass

    if not section:
        if target_date:
            section = _section_for_date(target_date, today)
        else:
            return "Where should I move them? Try: <b>move everything overdue to next week</b>"

    if section not in ("today", "week", "month", "later"):
        section = "later"

    scheduled_at = None
    if target_date:
        scheduled_at = datetime.combine(target_date, datetime.min.time())

    with SessionLocal() as db:
        base = db.query(models.Card).filter(
            models.Card.completed == False,  # noqa: E712
            models.Card.archived == False,   # noqa: E712
        )

        if filter_type == "overdue":
            candidates = [
                c for c in base.filter(
                    models.Card.section == "today",
                    models.Card.scheduled_at.isnot(None),
                ).all()
                if c.scheduled_at.date() < today
            ]
        elif filter_type == "today":
            candidates = base.filter(models.Card.section == "today").all()
        elif filter_type == "week":
            candidates = base.filter(models.Card.section == "week").all()
        else:  # all_incomplete
            candidates = base.filter(models.Card.section != "none").all()

        filter_labels = {
            "overdue": "overdue tasks", "today": "tasks in Today",
            "week": "tasks in This Week", "all_incomplete": "incomplete tasks",
        }
        if not candidates:
            return f"No {filter_labels.get(filter_type, 'tasks')} found."

        undo_data = []
        # "completed" is deliberately absent from this data dict -- a bulk
        # reschedule must never trigger update_card_row's recurrence-spawn.
        data = {"section": section}
        if target_date:
            data["scheduled_at"] = scheduled_at
        for card in candidates:
            undo_data.append({
                "card_id": card.id, "title": card.title,
                "old_section": card.section, "old_scheduled_at": card.scheduled_at,
            })
            update_card_row(db, card, dict(data), None, today)

    section_label = {"today": "Today", "week": "This Week", "month": "This Month", "later": "Later"}.get(section, section)
    n = len(candidates)

    if chat_id:
        session = _get_session(chat_id)
        _push_undo(session, {"type": "bulk_reschedule", "cards": undo_data})

    date_part = f" ({target_date.strftime('%A, %b %-d')})" if target_date else ""
    return f"↗ Moved {n} task{'s' if n != 1 else ''} to <b>{section_label}</b>{date_part}\nSend <b>undo</b> to reverse."


def _fuzzy_match(items: list, key, query: str) -> list:
    """Return best-matching items (up to 3) for disambiguation, where
    `key(item)` extracts the name/title to match against.

    Strategy: exact → substring (either direction) → word overlap.
    A single exact or unambiguous substring match returns a one-element list.
    Multiple equally-good matches return up to 3 for the caller to disambiguate.

    Shared by card matching (_fuzzy_find_cards) and habit matching
    (_fuzzy_find_habits) -- the two used to be separate, subtly different
    implementations of the same algorithm.
    """
    q = query.lower().strip()
    q_words = {w for w in q.split() if len(w) > 2}

    # 1. Exact match — always unambiguous
    for item in items:
        if key(item).lower() == q:
            return [item]

    # 2. Substring: query in name, or name in query
    sub = [item for item in items if q in key(item).lower() or key(item).lower() in q]
    if sub:
        return sub[:3]

    # 3. Word overlap — most shared words wins; ties kept for disambiguation
    if q_words:
        scored = [(len(q_words & set(key(item).lower().split())), item) for item in items]
        scored = [(s, item) for s, item in scored if s > 0]
        if scored:
            scored.sort(key=lambda x: -x[0])
            top = scored[0][0]
            return [item for s, item in scored if s == top][:3]

    return []


def _fuzzy_find_cards(cards: list, query: str) -> list:
    return _fuzzy_match(cards, lambda c: c.title, query)


def _fuzzy_find_habits(habits: list, query: str) -> list:
    return _fuzzy_match(habits, lambda h: h.name, query)


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
            today = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)).date()
            update_card_row(db, card, {"completed": True}, None, today)
        _push_undo(session, {"type": "mark_complete", "card_id": selected["id"], "title": selected["title"]})
        session["last_card"] = {"id": selected["id"], "title": selected["title"]}
        return f'✓ Marked complete: <b>{selected["title"]}</b>\nSend <b>undo</b> to reverse.'

    if action == "reschedule":
        intent = dict(pending["intent"])
        intent["match_query"] = selected["title"]  # exact title → unambiguous re-run
        return _reply_reschedule(intent, tz_offset, chat_id)

    if action == "add_note":
        note = pending["intent"].get("note", "")
        with SessionLocal() as db:
            card = db.query(models.Card).filter_by(id=selected["id"]).first()
            if not card:
                return f'"{selected["title"]}" not found.'
            old_description = card.description or ""
            new_description = (old_description + "\n" + note).strip() if old_description else note
            title = card.title
            card = update_card_row(db, card, {"description": new_description}, None, datetime.now(timezone.utc).date())
            card_id = card.id
        _push_undo(session, {"type": "add_note", "card_id": card_id, "title": title, "old_description": old_description})
        session["last_card"] = {"id": card_id, "title": title}
        return f'📝 Note added to <b>{title}</b>\nSend <b>undo</b> to remove it.'

    if action == "read_note":
        with SessionLocal() as db:
            card = db.query(models.Card).filter_by(id=selected["id"]).first()
            if not card:
                return f'"{selected["title"]}" not found.'
            title = card.title
            description = card.description
            card_id = card.id
        session["last_card"] = {"id": card_id, "title": title}
        if not description:
            return f'<b>{title}</b> has no notes.'
        return f'<b>{title}</b>\n\n{description}'

    if action == "queue_bridge":
        intent = dict(pending["intent"])
        intent["match_query"] = selected["title"]
        return _reply_queue_bridge(intent, chat_id)

    return None


def _reply_complete(intent: dict, tz_offset: int, chat_id: str = "") -> str:
    query = intent.get("match_query") or ""
    if not query:
        return "What task should I mark complete? Try: <b>done [task name]</b>"

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
        title = match.title
        today = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)).date()
        match = update_card_row(db, match, {"completed": True}, None, today)
        card_id = match.id

    if chat_id:
        _push_undo(session, {"type": "mark_complete", "card_id": card_id, "title": title})
        session["last_card"] = {"id": card_id, "title": title}

    return f"✓ Marked complete: <b>{title}</b>\nSend <b>undo</b> to reverse."


_SECTION_LABELS = {"today": "Today", "week": "This Week", "month": "This Month", "later": "Later"}


def _resolve_tag_ids(db, tag_names: list[str]) -> list[int]:
    """Case-insensitive match against existing tags; create any that don't
    exist yet. Mirrors QuickAddModal.jsx's resolveNewTags on the frontend."""
    if not tag_names:
        return []
    by_lower = {t.name.lower(): t for t in db.query(models.Tag).all()}
    ids = []
    for name in tag_names:
        tag = by_lower.get(name.lower())
        if not tag:
            tag = models.Tag(name=name)
            db.add(tag)
            db.flush()
            by_lower[name.lower()] = tag
        ids.append(tag.id)
    return ids


def _capture_from_text(text: str, tz_offset: int, chat_id: str = "") -> str:
    """Create one or more items from free text via the same shared parsing
    pipeline the webapp's Quick Add uses (routers.cards.parse_bulk_text) --
    splitting multi-item messages, resolving dates, and applying the same
    deterministic corrections -- rather than Telegram extracting title/
    section/date itself from a single classification call with no
    algorithmic fallback. Each parsed item is then dispatched by type,
    reusing existing Telegram capability handlers for food/mood/habit_check/
    task_complete (already correct) and the same DB-creation logic the
    webapp's own endpoints use for task/habit/goal."""
    from routers.cards import parse_bulk_text, create_card_row
    from routers.habits import create_habit_row
    from routers.withings import withings_set_goals
    from capabilities.workout import parse_workout

    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    try:
        with SessionLocal() as db:
            items = parse_bulk_text(db, text, today)
    except Exception as e:
        print(f"[telegram] capture parse failed: {e}")
        items = []

    if not items:
        return _capture_plain(text, tz_offset, chat_id)

    blocks = []
    card_ids = []
    habit_ids = []
    workout_ids = []
    last_card = None

    with SessionLocal() as db:
        for item in items:
            if item.type == "task":
                title = item.title.strip() or text.strip()
                tag_ids = _resolve_tag_ids(db, item.suggested_tags)
                data = {
                    "title": title,
                    "description": item.description,
                    "section": item.section,
                    "scheduled_at": item.scheduled_at,
                    "recurrence_rule": item.recurrence_rule,
                    "raw_input": text,
                }
                card = create_card_row(db, data, tag_ids)
                card_ids.append(card.id)
                last_card = {"id": card.id, "title": title}
                blocks.append(f"✓ Added to <b>{_SECTION_LABELS.get(card.section, card.section)}</b>: {title}")

            elif item.type == "habit":
                tag_ids = _resolve_tag_ids(db, item.suggested_tags)
                habit = create_habit_row(db, item.title, item.health_metric, item.health_goal, tag_ids)
                habit_ids.append(habit.id)
                blocks.append(f"✓ New habit: {item.title}")

            elif item.type == "goal" and item.health_metric == "steps" and item.health_goal is not None:
                habit = create_habit_row(db, "Daily Steps", "steps", item.health_goal, [])
                habit_ids.append(habit.id)
                blocks.append(f"✓ Step goal set: {item.health_goal:g}")

            elif item.type == "goal" and item.health_metric and item.health_goal is not None:
                withings_set_goals({item.health_metric: item.health_goal}, db)
                blocks.append(f"✓ {item.health_metric} goal set: {item.health_goal:g}")

            elif item.type == "workout":
                raw = item.source_text or item.title
                parsed = parse_workout(raw)
                entry = models.WorkoutEntry(raw_input=raw, logged_at=now_local, **parsed)
                db.add(entry)
                db.flush()
                workout_ids.append(entry.id)
                blocks.append(f"✓ Workout logged: {raw}")

            elif item.type == "assist":
                blocks.append(f"That sounded like a question, not something to capture: \"{item.title}\" — try asking me directly.")

            # food / mood / habit_check / task_complete are delegated below,
            # outside this session, to the existing Telegram handlers that
            # already do this correctly (including their own undo).
        db.commit()

    for item in items:
        if item.type == "food":
            blocks.append(_reply_log_food({"raw_input": item.source_text or item.title}, tz_offset, chat_id))
        elif item.type == "mood":
            blocks.append(_reply_log_mood({"energy": item.energy, "note": item.description}, tz_offset, chat_id))
        elif item.type == "habit_check":
            blocks.append(_reply_complete_habit({"match_query": item.title}, tz_offset, chat_id))
        elif item.type == "task_complete":
            blocks.append(_reply_complete({"match_query": item.title}, tz_offset, chat_id))

    if chat_id and (card_ids or habit_ids or workout_ids):
        session = _get_session(chat_id)
        _push_undo(session, {
            "type": "capture_multi", "card_ids": card_ids, "habit_ids": habit_ids,
            "workout_ids": workout_ids,
            "title": items[0].title if len(items) == 1 else text,
        })
        if last_card:
            session["last_card"] = last_card

    if not blocks:
        return _capture_plain(text, tz_offset, chat_id)
    if card_ids and len(blocks) == 1:
        return blocks[0] + "\nSend <b>undo</b> to remove it."
    return "\n".join(blocks) + "\nSend <b>undo</b> to reverse the most recent action."


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
        _push_undo(session, {"type": "capture", "card_id": card_id, "title": text})
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
        for ev in sorted(items["events"], key=lambda e: (e["all_day"], e["start"])):
            day_lines.append(f"  📅 {ev['title']} @ {ev['time_str']}")
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


def _reply_weather(chat_id: str = "") -> str:
    """Current weather, resolving location in order: a location shared in this
    Telegram chat recently, else the webapp's last-known lat/lon (persisted by
    briefing/generate.py's _fetch_briefing_data specifically for "no browser
    available" cases like this one), else a hint to share one."""
    from weather import fetch_weather

    lat = lon = None
    used_webapp_location = False

    if chat_id:
        loc = _get_session(chat_id).get("last_location")
        if loc:
            lat, lon = loc["lat"], loc["lon"]

    if lat is None or lon is None:
        with SessionLocal() as db:
            s = Settings(db)
            lat, lon = s.last_known_lat, s.last_known_lon
        used_webapp_location = lat is not None and lon is not None

    if lat is None or lon is None:
        return (
            "I don't know your location yet — open the web app once with location "
            "permission granted, or share your location in this chat."
        )

    result = fetch_weather(lat, lon)
    if not result:
        return "Couldn't fetch the weather right now — try again in a bit."

    lines = [f"<b>{result['emojis']} {result['description'].capitalize()}</b>"]
    lines.append(f"High {result['high']}°F / Low {result['low']}°F")
    if result.get("umbrella"):
        lines.append("☂️ Bring an umbrella")
    if result.get("snow"):
        lines.append("❄️ Snow expected")
    if result.get("cold"):
        lines.append("🧥 Bundle up, it's cold out")
    if used_webapp_location:
        lines.append("<i>(using your last known location from the app)</i>")
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
        if not ev["all_day"] and ev["start"].replace(tzinfo=None) - timedelta(minutes=tz_offset) >= now_local
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
            ctx_lines.append(f"  - {ev['title']} at {ev['time_str']}")
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
            return f"🎯 You have <b>{upcoming_events[0]['title']}</b> coming up — prepare for that."
        return f"🎯 Start with: <b>{today_cards[0].title}</b>"

    return f"🎯 {suggestion}"


def _reply_ask_schedule(intent: dict, tz_offset: int) -> str:
    """Answer a question that needs reasoning over today's schedule (gaps,
    duration, "what's the last/first thing", availability) rather than a raw
    listing -- query_schedule/_reply_date covers the listing case. Modeled
    directly on _reply_priority: gather context, one LLM call, 1-2 sentences.
    Unlike _reply_priority (which only looks at what's still upcoming, since
    it's answering "what should I do right now"), this includes the WHOLE
    day so questions like "what was my last thing today" still work later
    in the day."""
    question = (intent.get("question") or "").strip()
    if not question:
        return "What would you like to know about your schedule?"

    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    with SessionLocal() as db:
        today_cards = (
            db.query(models.Card)
            .filter(
                models.Card.completed == False,  # noqa: E712
                models.Card.archived == False,   # noqa: E712
            )
            .all()
        )
    timed_cards = sorted(
        [c for c in today_cards if c.scheduled_at and c.scheduled_at.date() == today],
        key=lambda c: c.scheduled_at,
    )
    untimed_cards = [c for c in today_cards if c.section == "today" and not c.scheduled_at]

    cal_events = sorted(
        _fetch_cal_events_for_date(today, tz_offset),
        key=lambda e: (e["all_day"], e["start"]),
    )

    if not timed_cards and not untimed_cards and not cal_events:
        return "Nothing on your schedule today — ask away, but there's nothing to reason about!"

    # Merge timed events + timed tasks into one chronological timeline and
    # precompute every gap in Python -- verified directly that the LLM is
    # unreliable at this: asked "how many minutes until my next thing" with a
    # clean, unambiguous 60-minute gap in the context and it answered
    # "approximately 0". Handing it ready-made numbers instead of raw
    # timestamps turns the LLM's job into phrasing, not arithmetic.
    timeline = [
        (ev["start"].replace(tzinfo=None) - timedelta(minutes=tz_offset), ev["title"])
        for ev in cal_events if not ev["all_day"]
    ]
    timeline += [(c.scheduled_at, c.title) for c in timed_cards]
    timeline.sort(key=lambda x: x[0])

    ctx_lines = [f"Current time: {now_local.strftime('%-I:%M %p')} on {today.strftime('%A, %B %-d')}"]

    all_day_events = [ev["title"] for ev in cal_events if ev["all_day"]]
    if all_day_events:
        ctx_lines.append("All-day events today: " + ", ".join(all_day_events))

    if timeline:
        ctx_lines.append(
            "Today's timed items, chronological -- gaps are already computed for you "
            "in brackets, use those numbers directly rather than calculating your own:"
        )
        for i, (t, label) in enumerate(timeline):
            if t >= now_local:
                status = f"{round((t - now_local).total_seconds() / 60)} min from now"
            else:
                status = "already passed"
            next_gap = ""
            if i + 1 < len(timeline):
                gap = round((timeline[i + 1][0] - t).total_seconds() / 60)
                next_gap = f", {gap} min before {timeline[i + 1][1]}"
            ctx_lines.append(f"  - {label} at {t.strftime('%-I:%M %p')} [{status}{next_gap}]")

    if untimed_cards:
        ctx_lines.append("Other tasks today with no specific time:")
        for c in untimed_cards:
            ctx_lines.append(f"  - {c.title}")

    # Duration-fit questions ("do I have time for a 20 minute nap") need a
    # yes/no conclusion, not just a number -- and that comparison turned out
    # to be exactly where this model falls down, badly: given a clean,
    # unambiguous 60-minute gap and a stated 20-minute duration, it answered
    # "No" consistently across repeated tries, with the gap number stated
    # correctly in the same sentence as the wrong verdict -- and it kept
    # answering "No" even when handed the correct verdict verbatim as an
    # "ANSWER: YES" context line with an explicit "never contradict this"
    # instruction. Not a data or prompting problem, a hard capability
    # ceiling for this model. So this reply is generated directly in
    # Python rather than trusting the LLM's conclusion at all -- correctness
    # matters more than natural phrasing for a yes/no the user acts on.
    duration = intent.get("duration_minutes")
    try:
        duration = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if duration is not None:
        next_future = next(((t, label) for t, label in timeline if t >= now_local), None)
        if next_future is None:
            return f"✓ Yes — nothing else is scheduled for the rest of today, plenty of room for {duration} min."
        next_time, next_label = next_future
        gap_minutes = round((next_time - now_local).total_seconds() / 60)
        when = next_time.strftime('%-I:%M %p')
        if gap_minutes >= duration:
            return f"✓ Yes — {gap_minutes} min free before {next_label} at {when}, plenty of room for {duration} min."
        return f"✗ Not quite — only {gap_minutes} min before {next_label} at {when}, and {duration} min is needed."

    system = (
        "You are a personal scheduling assistant. Answer the user's question directly "
        "and specifically, using ONLY the schedule data given below -- never invent "
        "anything not listed. Gap and time-until numbers are already computed for you "
        "in brackets next to each item -- read them off directly, do not recompute your "
        "own subtraction, you will get it wrong. "
        "Reply in 1-2 sentences max -- be direct and specific. No lists, no bullet "
        "points. If there truly isn't enough information to answer, say so briefly."
    )
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"{chr(10).join(ctx_lines)}\n\nQuestion: {question}"},
            ],
            timeout=15,
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[telegram] ask_schedule LLM error: {e}")
        return "Couldn't work that out right now — try <b>today</b> to see your full schedule instead."


_ENERGY_LABELS = {1: "😴 Drained", 2: "😔 Low", 3: "😐 Okay", 4: "😊 Good", 5: "⚡ Energized"}


def _reply_log_mood(intent: dict, tz_offset: int, chat_id: str = "") -> str:
    """Log today's energy level."""
    try:
        energy = max(1, min(5, int(float(str(intent.get("energy") or "3")))))
    except (ValueError, TypeError):
        energy = 3
    note = intent.get("note") or None

    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today_str = now_local.date().isoformat()

    with SessionLocal() as db:
        row = db.query(models.MoodLog).filter_by(date=today_str).first()
        prev_energy = row.energy if row else None
        prev_note   = row.note   if row else None
        if row:
            row.energy = energy
            row.note = note
            row.updated_at = datetime.now(timezone.utc)
        else:
            db.add(models.MoodLog(date=today_str, energy=energy, note=note))
        db.commit()

    if chat_id:
        session = _get_session(chat_id)
        _push_undo(session, {
            "type": "mood_log",
            "date": today_str,
            "prev_energy": prev_energy,
            "prev_note": prev_note,
            "title": "energy log",
        })

    label = _ENERGY_LABELS.get(energy, "😐 Okay")
    note_part = f" — {note}" if note else ""
    return f"✓ Energy logged: <b>{label}</b>{note_part}\nSend <b>undo</b> to remove it."


def _reply_log_food(intent: dict, tz_offset: int, chat_id: str = "") -> str:
    """Log one or more food/drink entries. Splits multi-item messages ("had a
    bagel, coffee, and a banana") and enriches each with category/notes/
    quality/calories via parse_food_entries() -- the same function the
    webapp's food log uses, so both surfaces behave identically."""
    raw = (intent.get("raw_input") or "").strip()
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)

    parsed_items = parse_food_entries(raw)

    entry_ids = []
    names = []
    with SessionLocal() as db:
        for parsed in parsed_items:
            entry = models.FoodEntry(
                raw_input=parsed.pop("source_text", None) or raw,
                consumed_at=now_local,
                **parsed,
            )
            db.add(entry)
            db.flush()
            entry_ids.append(entry.id)
            names.append(parsed["name"])
        db.commit()

    if chat_id:
        session = _get_session(chat_id)
        _push_undo(session, {"type": "food_log", "entry_ids": entry_ids, "title": ", ".join(names)})

    if len(names) == 1:
        return f"✓ Food logged: {names[0]}\nSend <b>undo</b> to remove it."
    items_list = "\n".join(f"  • {n}" for n in names)
    return f"✓ {len(names)} food items logged:\n{items_list}\nSend <b>undo</b> to remove them."


def _reply_log_workout(intent: dict, tz_offset: int, chat_id: str = "") -> str:
    """Log a workout entry, parsed via the same LLM enrichment call the
    webapp's workout log uses (capabilities.workout.parse_workout) instead of
    trusting type/value/unit straight from the intent-classification call --
    the same fix already applied to food logging this session. Gets the
    `notes` field for free, which the old one-call shape never populated."""
    raw = (intent.get("raw_input") or "").strip()
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    parsed = parse_workout(raw)

    with SessionLocal() as db:
        entry = models.WorkoutEntry(raw_input=raw, logged_at=now_local, **parsed)
        db.add(entry)
        db.commit()
        entry_id = entry.id

    if chat_id:
        session = _get_session(chat_id)
        _push_undo(session, {"type": "workout_log", "entry_id": entry_id, "title": raw})

    value, unit = parsed["value"], parsed["unit"]
    detail = f" ({value:g} {unit})" if value is not None and unit else ""
    return f"✓ Workout logged{detail}: {raw}\nSend <b>undo</b> to remove it."


def _reply_complete_habit(intent: dict, tz_offset: int, chat_id: str = "") -> str:
    """Mark a habit complete for today."""
    query = intent.get("match_query") or ""
    if not query:
        return "Which habit did you complete? Try: <b>done meditation</b>"

    from streak import recompute_from
    now_local = datetime.now(timezone.utc).replace(tzinfo=None)  # rough — used only for date
    with SessionLocal() as db:
        s = Settings(db)
        tz_offset = s.tz_offset
        today = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)).date()
        today_str = today.isoformat()

        habits = db.query(models.Habit).filter_by(archived=False).all()

        # No disambiguation UI for habits today (unlike cards) -- just take
        # the best match; ties keep the first-encountered habit, same as
        # the old hand-inlined version of this matcher did.
        matches = _fuzzy_find_habits(habits, query)
        match = matches[0] if matches else None

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


# Wire the capability handlers into the registry now that they're all defined --
# _route_message looks these up via by_telegram_action() instead of a hand-maintained
# if/elif chain, so a new capability only needs a set_telegram_handler call here.
set_telegram_handler("food", _reply_log_food)
set_telegram_handler("habit_check", _reply_complete_habit)
set_telegram_handler("mood", _reply_log_mood)
set_telegram_handler("task_complete", _reply_complete)
set_telegram_handler("workout", _reply_log_workout)


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
        title = match.title

        # "completed" is deliberately absent from this data dict -- a
        # reschedule must never trigger update_card_row's recurrence-spawn.
        data = {}
        if section:
            data["section"] = section
        if target_date:
            data["scheduled_at"] = scheduled_at  # may be None if no time given
        match = update_card_row(db, match, data, None, today)
        card_id = match.id

    if chat_id:
        _push_undo(session, {
            "type": "reschedule",
            "card_id": card_id,
            "title": title,
            "old_section": old_section,
            "old_scheduled_at": old_scheduled_at,
        })
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


def _undo_single_action(action: dict) -> str:
    """Execute one undo action and return the confirmation line."""
    atype = action["type"]
    title = action.get("title", "")

    if atype == "capture":
        with SessionLocal() as db:
            card = db.query(models.Card).filter_by(id=action["card_id"]).first()
            if not card:
                return f'Could not undo — "{title}" no longer exists.'
            db.delete(card)
            db.commit()
        return f"↩ Removed: <b>{title}</b>"

    if atype == "capture_multi":
        with SessionLocal() as db:
            removed = 0
            if action.get("card_ids"):
                removed += db.query(models.Card).filter(
                    models.Card.id.in_(action["card_ids"])
                ).delete(synchronize_session=False)
            if action.get("habit_ids"):
                removed += db.query(models.Habit).filter(
                    models.Habit.id.in_(action["habit_ids"])
                ).delete(synchronize_session=False)
            if action.get("workout_ids"):
                removed += db.query(models.WorkoutEntry).filter(
                    models.WorkoutEntry.id.in_(action["workout_ids"])
                ).delete(synchronize_session=False)
            db.commit()
        if not removed:
            return f'Could not undo — "{title}" no longer exists.'
        return f"↩ Removed: <b>{title}</b>"

    if atype == "mark_complete":
        with SessionLocal() as db:
            card = db.query(models.Card).filter_by(id=action["card_id"]).first()
            if not card:
                return f'Could not undo — "{title}" no longer exists.'
            if not card.completed:
                return f'"{title}" is already not marked complete.'
            # "completed": False never triggers recurrence-spawn (that only
            # fires transitioning False->True) -- safe to route through the
            # shared function even here.
            update_card_row(db, card, {"completed": False}, None, datetime.now(timezone.utc).date())
        return f"↩ Unmarked complete: <b>{title}</b>"

    if atype == "reschedule":
        with SessionLocal() as db:
            card = db.query(models.Card).filter_by(id=action["card_id"]).first()
            if not card:
                return f'Could not undo — "{title}" no longer exists.'
            data = {"section": action["old_section"], "scheduled_at": action["old_scheduled_at"]}
            update_card_row(db, card, data, None, datetime.now(timezone.utc).date())
        return f"↩ Moved <b>{title}</b> back to {action['old_section'].title()}"

    if atype == "mood_log":
        with SessionLocal() as db:
            row = db.query(models.MoodLog).filter_by(date=action["date"]).first()
            if row:
                if action.get("prev_energy") is None:
                    db.delete(row)
                else:
                    row.energy = action["prev_energy"]
                    row.note   = action.get("prev_note")
                db.commit()
        return "↩ Energy log removed"

    if atype == "food_log":
        with SessionLocal() as db:
            entries = db.query(models.FoodEntry).filter(
                models.FoodEntry.id.in_(action["entry_ids"])
            ).all()
            for entry in entries:
                db.delete(entry)
            db.commit()
        label = "food log" if len(action["entry_ids"]) == 1 else f"{len(action['entry_ids'])} food logs"
        return f"↩ Removed {label}: <b>{title}</b>"

    if atype == "workout_log":
        with SessionLocal() as db:
            entry = db.query(models.WorkoutEntry).filter_by(id=action["entry_id"]).first()
            if entry:
                db.delete(entry)
                db.commit()
        return f"↩ Removed workout log: <b>{title}</b>"

    if atype == "add_note":
        with SessionLocal() as db:
            card = db.query(models.Card).filter_by(id=action["card_id"]).first()
            if not card:
                return f'Could not undo — "{title}" no longer exists.'
            data = {"description": action.get("old_description") or None}
            update_card_row(db, card, data, None, datetime.now(timezone.utc).date())
        return f"↩ Note removed from: <b>{title}</b>"

    if atype == "bulk_reschedule":
        cards_data = action.get("cards", [])
        count = 0
        today = datetime.now(timezone.utc).date()
        for item in cards_data:
            with SessionLocal() as db:
                card = db.query(models.Card).filter_by(id=item["card_id"]).first()
                if card:
                    data = {"section": item["old_section"], "scheduled_at": item["old_scheduled_at"]}
                    update_card_row(db, card, data, None, today)
                    count += 1
        return f"↩ Moved {count} task{'s' if count != 1 else ''} back"

    return "↩ Undone"


def _reply_queue_bridge(intent: dict, chat_id: str = "") -> str:
    """Queue a Claude Code bridge job for a card."""
    query = (intent.get("match_query") or "").strip()
    if not query:
        return "Which card? Try: <b>/build auth feature</b>"

    session = _get_session(chat_id) if chat_id else {}

    if query.lower() in _PRONOUN_REFS and session.get("last_card"):
        query = session["last_card"]["title"]

    with SessionLocal() as db:
        # Support numeric ID lookup (e.g. "/build 42")
        card = None
        if query.isdigit():
            card = db.query(models.Card).filter_by(id=int(query), archived=False).first()

        if card is None:
            cards = db.query(models.Card).filter_by(archived=False).all()
            matches = _fuzzy_find_cards(cards, query)
            if not matches:
                return f'Couldn\'t find a card matching "{query}". Try <b>today</b> to see your list.'
            if len(matches) > 1:
                if chat_id:
                    session["pending"] = {
                        "action": "queue_bridge",
                        "intent": intent,
                        "candidates": [{"id": c.id, "title": c.title} for c in matches],
                    }
                numbered = "\n".join(f"{i + 1}. {c.title}" for i, c in enumerate(matches))
                return f"Which card?\n{numbered}"
            card = matches[0]

        if not card.spec:
            return (
                f'<b>{card.title}</b> has no spec yet.\n'
                'Open the card in the app and generate a spec first, then try again.'
            )

        now = datetime.now(timezone.utc)
        job = models.BridgeJob(
            card_id=card.id,
            status="pending",
            spec_snapshot=card.spec,
            created_at=now,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        title = card.title
        card_id = card.id
        job_id = job.id

    if chat_id:
        session["last_card"] = {"id": card_id, "title": title}

    return (
        f'Queued build job #{job_id} for <b>{title}</b>.\n'
        'The bridge agent will pick it up next time it polls.\n'
        'I\'ll notify you when it\'s done.'
    )


def _reply_undo(chat_id: str, count: int = 1) -> str:
    """Reverse the most recent reversible action(s) for this chat."""
    session = _get_session(chat_id)

    to_undo = []
    if session.get("undo"):
        to_undo.append(session["undo"])
        session["undo"] = None
    if count >= 2 and session.get("undo_prev"):
        to_undo.append(session["undo_prev"])
        session["undo_prev"] = None

    if not to_undo:
        return "Nothing to undo."

    return "\n".join(_undo_single_action(a) for a in to_undo)
