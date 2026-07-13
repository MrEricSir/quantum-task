import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import app_setting_keys as setting_keys
import models
import telegram_notify
from deps import get_db, llm_client, LLM_MODEL
from routers.briefing import generate_today_briefing

log = logging.getLogger(__name__)

router = APIRouter()


class _TelegramConfig(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    schedule_time: str = "07:30"
    tz_offset: int = 0  # JS convention: UTC+10 → -600, UTC-5 → +300
    habit_reminder_time: str = ""   # "HH:MM" local or "" to disable
    overdue_nudge_time: str = ""    # "HH:MM" local or "" to disable


def _get(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.AppSetting).filter_by(key=key).first()
    return row.value if row and row.value else default


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(models.AppSetting).filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.add(models.AppSetting(key=key, value=value))


@router.get("/api/telegram/config")
def get_telegram_config(db: Session = Depends(get_db)):
    return {
        "bot_token":           _get(db, setting_keys.TELEGRAM_BOT_TOKEN),
        "chat_id":             _get(db, setting_keys.TELEGRAM_CHAT_ID),
        "schedule_time":       _get(db, setting_keys.BRIEFING_SCHEDULE_TIME, "07:30"),
        "tz_offset":           int(_get(db, setting_keys.BRIEFING_TZ_OFFSET, "0") or "0"),
        "habit_reminder_time": _get(db, setting_keys.HABIT_REMINDER_TIME, ""),
        "overdue_nudge_time":  _get(db, setting_keys.OVERDUE_NUDGE_TIME, ""),
    }


@router.put("/api/telegram/config")
def save_telegram_config(body: _TelegramConfig, db: Session = Depends(get_db)):
    _set(db, setting_keys.TELEGRAM_BOT_TOKEN,     body.bot_token.strip())
    _set(db, setting_keys.TELEGRAM_CHAT_ID,       body.chat_id.strip())
    _set(db, setting_keys.BRIEFING_SCHEDULE_TIME, body.schedule_time.strip() or "07:30")
    _set(db, setting_keys.BRIEFING_TZ_OFFSET,     str(body.tz_offset))
    _set(db, setting_keys.HABIT_REMINDER_TIME,    body.habit_reminder_time.strip())
    _set(db, setting_keys.OVERDUE_NUDGE_TIME,     body.overdue_nudge_time.strip())
    db.commit()
    return {"ok": True}


@router.post("/api/telegram/daily-briefing")
def daily_briefing(db: Session = Depends(get_db)):
    """Called by Cloud Scheduler hourly. Runs all scheduled notification checks."""
    token   = _get(db, setting_keys.TELEGRAM_BOT_TOKEN)
    chat_id = _get(db, setting_keys.TELEGRAM_CHAT_ID)
    if not token or not chat_id:
        return {"ok": False, "skipped": True, "reason": "not configured"}

    tz_offset = int(_get(db, setting_keys.BRIEFING_TZ_OFFSET, "0") or "0")
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today     = now_local.date()

    results = {
        "briefing":      _check_briefing(db, token, chat_id, tz_offset, now_local, today),
        "habit_reminder": _check_habit_reminder(db, token, chat_id, now_local, today),
        "overdue_nudge":  _check_overdue_nudge(db, token, chat_id, now_local, today),
    }
    return {"ok": True, "results": results}


def _hour_matches(time_str: str, now_local: datetime) -> bool:
    """Return True if the configured HH:MM time matches the current local hour."""
    if not time_str:
        return False
    try:
        return int(time_str.split(":")[0]) == now_local.hour
    except Exception:
        return False


def _check_briefing(db, token: str, chat_id: str, tz_offset: int,
                    now_local: datetime, today) -> str:
    """Send the daily briefing if it's the right hour and not already sent."""
    schedule_time = _get(db, setting_keys.BRIEFING_SCHEDULE_TIME, "07:30")
    if not _hour_matches(schedule_time, now_local):
        return "skipped"

    last_sent = _get(db, setting_keys.BRIEFING_LAST_SENT)
    if last_sent == today.isoformat():
        return "already_sent"

    _set(db, setting_keys.BRIEFING_LAST_SENT, today.isoformat())
    db.commit()

    try:
        text = generate_today_briefing(today, tz_offset)
    except Exception as e:
        return f"error: {e}"

    if not text:
        return "error: LLM returned empty"

    return "sent" if telegram_notify.send_message(token, chat_id, text) else "send_failed"


def _check_habit_reminder(db, token: str, chat_id: str,
                           now_local: datetime, today) -> str:
    """Send an evening habit nudge if configured, it's the right hour, and habits are pending."""
    reminder_time = _get(db, setting_keys.HABIT_REMINDER_TIME, "")
    if not _hour_matches(reminder_time, now_local):
        return "skipped"

    last_sent = _get(db, setting_keys.HABIT_REMINDER_LAST_SENT)
    if last_sent == today.isoformat():
        return "already_sent"

    today_str = today.isoformat()
    habits = db.query(models.Habit).filter_by(archived=False).all()
    if not habits:
        return "skipped: no habits"

    completed_ids = {
        r.habit_id for r in db.query(models.HabitCompletion).filter_by(date=today_str).all()
    }
    pending = [h for h in habits if h.id not in completed_ids]
    if not pending:
        return "skipped: all done"

    _set(db, setting_keys.HABIT_REMINDER_LAST_SENT, today_str)
    db.commit()

    lines = [f"<b>🔔 Habit check-in</b> — {len(pending)} left today\n"]
    for h in pending:
        lines.append(f"○ {h.name}")
    text = "\n".join(lines)

    return "sent" if telegram_notify.send_message(token, chat_id, text) else "send_failed"


def _check_overdue_nudge(db, token: str, chat_id: str,
                          now_local: datetime, today) -> str:
    """Send a midday overdue-task nudge if configured and overdue tasks exist."""
    nudge_time = _get(db, setting_keys.OVERDUE_NUDGE_TIME, "")
    if not _hour_matches(nudge_time, now_local):
        return "skipped"

    last_sent = _get(db, setting_keys.OVERDUE_NUDGE_LAST_SENT)
    if last_sent == today.isoformat():
        return "already_sent"

    overdue = (
        db.query(models.Card)
        .filter(
            models.Card.completed == False,  # noqa: E712
            models.Card.archived == False,   # noqa: E712
            models.Card.section == "today",
            models.Card.scheduled_at.isnot(None),
        )
        .all()
    )
    overdue = [c for c in overdue if c.scheduled_at.date() < today]
    if not overdue:
        return "skipped: none overdue"

    _set(db, setting_keys.OVERDUE_NUDGE_LAST_SENT, today.isoformat())
    db.commit()

    lines = [f"<b>⚠ {len(overdue)} overdue task{'s' if len(overdue) != 1 else ''}</b>\n"]
    for c in sorted(overdue, key=lambda x: x.scheduled_at):
        days = (today - c.scheduled_at.date()).days
        lines.append(f"• {c.title} ({days}d)")
    text = "\n".join(lines)

    return "sent" if telegram_notify.send_message(token, chat_id, text) else "send_failed"


@router.post("/api/telegram/test")
def test_telegram(db: Session = Depends(get_db)):
    """Send the today briefing immediately as a test."""
    token   = _get(db, setting_keys.TELEGRAM_BOT_TOKEN)
    chat_id = _get(db, setting_keys.TELEGRAM_CHAT_ID)
    tz_offset = int(_get(db, setting_keys.BRIEFING_TZ_OFFSET, "0") or "0")

    if not token or not chat_id:
        return {"ok": False, "error": "Bot token and chat ID must be configured first."}

    today = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)).date()
    try:
        text = generate_today_briefing(today, tz_offset)
    except Exception as e:
        return {"ok": False, "error": f"Briefing generation error: {e}"}

    if not text:
        return {"ok": False, "error": "Could not generate briefing (LLM error)."}

    ok = telegram_notify.send_message(token, chat_id, text)
    if not ok:
        return {"ok": False, "error": "Message failed. Check that the bot token and chat ID are correct."}
    return {"ok": True}


# ── Webhook registration ───────────────────────────────────────────────────────

@router.post("/api/telegram/register-webhook")
def register_webhook(request: Request, db: Session = Depends(get_db)):
    """Register this backend as the Telegram webhook. Called from the settings UI."""
    token = _get(db, setting_keys.TELEGRAM_BOT_TOKEN)
    if not token:
        return {"ok": False, "error": "Bot token not configured."}

    # Generate or reuse a secret token for request verification
    secret = _get(db, setting_keys.TELEGRAM_WEBHOOK_SECRET)
    if not secret:
        secret = secrets.token_hex(32)
        _set(db, setting_keys.TELEGRAM_WEBHOOK_SECRET, secret)
        db.commit()

    # Derive our own public URL from the incoming request.
    # Force https — Cloud Run terminates TLS so base_url arrives as http internally.
    base = str(request.base_url).rstrip("/").replace("http://", "https://", 1)
    webhook_url = f"{base}/api/telegram/webhook"

    try:
        result = telegram_notify.set_webhook(token, webhook_url, secret)
    except Exception as e:
        return {"ok": False, "error": f"Telegram API error: {e}"}

    if not result.get("ok"):
        return {"ok": False, "error": result.get("description", "Unknown error")}

    return {"ok": True, "webhook_url": webhook_url}


# ── Incoming webhook ───────────────────────────────────────────────────────────

@router.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Receives incoming Telegram updates. Must return 200 quickly; processing runs in background."""
    secret = _get(db, setting_keys.TELEGRAM_WEBHOOK_SECRET)
    if secret and not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
        return {"ok": False}  # silently reject — don't leak info

    body = await request.json()
    background_tasks.add_task(_handle_update, body)
    return {"ok": True}


def _handle_update(update: dict) -> None:
    """Process a single Telegram update in a background task."""
    try:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        text = (msg.get("text") or "").strip()
        if not text:
            log.info("[telegram] update has no text, skipping")
            return
        chat_id_incoming = str(msg.get("chat", {}).get("id", ""))

        with SessionLocal() as db:
            token     = _get(db, setting_keys.TELEGRAM_BOT_TOKEN)
            chat_id   = _get(db, setting_keys.TELEGRAM_CHAT_ID)
            tz_offset = int(_get(db, setting_keys.BRIEFING_TZ_OFFSET, "0") or "0")

        if not token or not chat_id:
            log.warning("[telegram] bot not configured, dropping update")
            return
        if chat_id_incoming != chat_id:
            log.warning("[telegram] chat_id mismatch: got %s expected %s", chat_id_incoming, chat_id)
            return

        log.info("[telegram] routing message: %r", text[:80])
        reply = _route_message(text, tz_offset)
        ok = telegram_notify.send_message(token, chat_id, reply)
        log.info("[telegram] send_message ok=%s", ok)
    except Exception:
        log.exception("[telegram] unhandled error in _handle_update")


_TODAY_KEYWORDS = ("today", "list", "tasks", "my tasks", "my list", "schedule",
                   "what's on", "whats on", "what do i have", "show tasks")
_HABITS_KEYWORDS = ("habits", "my habits", "habit check", "habit status",
                    "how am i doing", "how are my habits")
_OVERDUE_KEYWORDS = ("overdue", "overdue tasks", "what's overdue", "whats overdue")
_COMPLETE_PREFIXES = ("done ", "complete ", "completed ", "finish ", "finished ", "done with ",
                      "mark done ", "mark complete ")


def _route_message(text: str, tz_offset: int) -> str:
    """Parse intent and return a reply string."""
    lower = text.lower().strip()

    # ── List today's tasks ────────────────────────────────────────────────────
    if any(lower == kw or lower.startswith(kw + " ") for kw in _TODAY_KEYWORDS):
        return _reply_today(tz_offset)

    # ── Habits status ─────────────────────────────────────────────────────────
    if any(lower == kw or lower.startswith(kw + " ") for kw in _HABITS_KEYWORDS):
        return _reply_habits(tz_offset)

    # ── Overdue tasks ─────────────────────────────────────────────────────────
    if any(lower == kw or lower.startswith(kw + " ") for kw in _OVERDUE_KEYWORDS):
        return _reply_overdue(tz_offset)

    # ── Mark a task complete ──────────────────────────────────────────────────
    for prefix in _COMPLETE_PREFIXES:
        if lower.startswith(prefix):
            query = text[len(prefix):].strip()
            return _reply_complete(query)

    # ── Help ──────────────────────────────────────────────────────────────────
    if lower in ("help", "/help", "/start"):
        return (
            "Hi! Here's what you can send me:\n\n"
            "<b>today</b> — show today's tasks\n"
            "<b>habits</b> — show today's habit status\n"
            "<b>overdue</b> — show overdue tasks\n"
            "<b>done [task]</b> — mark a task complete\n"
            "<b>anything else</b> — capture it as a new task\n\n"
            "You'll get your daily briefing and reminders automatically."
        )

    # ── Default: capture as new card ─────────────────────────────────────────
    return _reply_capture(text, tz_offset)


def _reply_today(tz_offset: int) -> str:
    """Return today's task list as a formatted message."""
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()

    with SessionLocal() as db:
        cards = (
            db.query(models.Card)
            .filter_by(section="today", completed=False, archived=False)
            .order_by(models.Card.position)
            .all()
        )

    if not cards:
        return "✓ Nothing on your plate today. Enjoy!"

    # Overdue = has a scheduled_at that is in the past
    overdue = [c for c in cards if c.scheduled_at and c.scheduled_at.date() < today]
    timed   = [c for c in cards if c.scheduled_at and c.scheduled_at.date() >= today]
    untimed = [c for c in cards if not c.scheduled_at]

    lines = ["<b>📋 Today</b>"]

    if overdue:
        lines.append("\n<b>⚠ Overdue</b>")
        for c in sorted(overdue, key=lambda x: x.scheduled_at):
            days = (today - c.scheduled_at.date()).days
            lines.append(f"• {c.title} ({days}d)")

    if timed:
        lines.append("\n<b>📅 Scheduled</b>")
        for c in sorted(timed, key=lambda x: x.scheduled_at):
            lines.append(f"• {c.title} @ {c.scheduled_at.strftime('%-I:%M %p')}")

    if untimed:
        lines.append("")
        for c in untimed:
            lines.append(f"• {c.title}")

    return "\n".join(lines)


def _reply_habits(tz_offset: int) -> str:
    """Return today's habit status."""
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
    """Return overdue tasks."""
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


def _reply_complete(query: str) -> str:
    """Find the best matching incomplete card and mark it complete."""
    query_lower = query.lower()

    with SessionLocal() as db:
        cards = db.query(models.Card).filter_by(completed=False, archived=False).all()
        # Exact match first, then partial
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
        title = match.title
        db.commit()

    return f"✓ Marked complete: <b>{title}</b>"


def _reply_capture(text: str, tz_offset: int) -> str:
    """Parse text with LLM and create a new card. Returns confirmation."""
    import json as _json

    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today = now_local.date()
    tomorrow = today + timedelta(days=1)

    with SessionLocal() as db:
        tag_names = [t.name for t in db.query(models.Tag).order_by(models.Tag.name).all()]

    tags_section = f"Available tags: {', '.join(tag_names)}" if tag_names else "No tags available."

    try:
        from model_plugins import get_plugin
        import schemas

        plugin = get_plugin(LLM_MODEL)
        prompt = plugin.get_system_prompt(
            today=today.isoformat(),
            weekday=today.strftime("%A"),
            tomorrow=tomorrow.isoformat(),
            tags_section=tags_section,
        )
        client = llm_client()
        response = client.chat.completions.create(
            model=plugin.model_name,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
        )
        raw = plugin.normalize_raw(_json.loads(response.choices[0].message.content))
        parsed = plugin.post_process(schemas.ParsedCard.model_validate(raw), text=text)
    except Exception as e:
        log.warning("LLM parse failed for Telegram capture: %s", e)
        # Fallback: create a plain card in today
        parsed = None

    with SessionLocal() as db:
        max_pos = db.query(models.Card).filter_by(section="today").count()
        if parsed:
            section = parsed.section if parsed.section not in (None, "none") else "today"
            card = models.Card(
                title=parsed.title or text,
                description=parsed.description,
                section=section,
                scheduled_at=parsed.scheduled_at,
                position=max_pos,
            )
            # Resolve tag names to Tag rows
            if parsed.tags:
                tag_objs = db.query(models.Tag).filter(models.Tag.name.in_(parsed.tags)).all()
                card.tags = tag_objs
        else:
            card = models.Card(title=text, section="today", position=max_pos)

        db.add(card)
        db.commit()
        section_label = {"today": "Today", "week": "This Week", "month": "This Month", "later": "Later"}.get(card.section, card.section)

    return f"✓ Added to <b>{section_label}</b>: {card.title}"


# Import here to avoid circular deps at module load time
from database import SessionLocal
