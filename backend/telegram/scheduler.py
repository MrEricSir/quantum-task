"""
Telegram scheduled notification checks.

Called every minute by the background task in main.py via check_all().
Each function is idempotent — it checks whether its condition is met and
whether it has already fired today before sending anything.
"""
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

import app_setting_keys as keys
import models
from settings import Settings
from telegram.notify import send_message


def _hour_matches(time_str: str, now_local: datetime) -> bool:
    """Return True if the HH:MM config string matches the current local hour."""
    if not time_str:
        return False
    try:
        return int(time_str.split(":")[0]) == now_local.hour
    except Exception:
        return False


def check_briefing(db: Session, token: str, chat_id: str,
                   tz_offset: int, now_local: datetime, today) -> str:
    """Send the daily briefing if it's the right hour and hasn't been sent yet."""
    from briefing import generate_today_briefing

    s = Settings(db)
    if not _hour_matches(s.briefing_schedule_time, now_local):
        return "skipped"
    if s.briefing_last_sent == today.isoformat():
        return "already_sent"

    s.set(keys.BRIEFING_LAST_SENT, today.isoformat())
    db.commit()

    try:
        text = generate_today_briefing(today, tz_offset)
    except Exception as e:
        return f"error: {e}"

    if not text:
        return "error: LLM returned empty"

    return "sent" if send_message(token, chat_id, text) else "send_failed"


def check_habit_reminder(db: Session, token: str, chat_id: str,
                          now_local: datetime, today) -> str:
    """Send an evening habit nudge if pending habits exist."""
    s = Settings(db)
    if not _hour_matches(s.habit_reminder_time, now_local):
        return "skipped"
    if s.habit_reminder_last_sent == today.isoformat():
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

    s.set(keys.HABIT_REMINDER_LAST_SENT, today_str)
    db.commit()

    lines = [f"<b>🔔 Habit check-in</b> — {len(pending)} left today\n"]
    for h in pending:
        lines.append(f"○ {h.name}")

    return "sent" if send_message(token, chat_id, "\n".join(lines)) else "send_failed"


def check_overdue_nudge(db: Session, token: str, chat_id: str,
                         now_local: datetime, today) -> str:
    """Send a midday overdue-task nudge if overdue tasks exist."""
    s = Settings(db)
    if not _hour_matches(s.overdue_nudge_time, now_local):
        return "skipped"
    if s.overdue_nudge_last_sent == today.isoformat():
        return "already_sent"

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
        return "skipped: none overdue"

    s.set(keys.OVERDUE_NUDGE_LAST_SENT, today.isoformat())
    db.commit()

    lines = [f"<b>⚠ {len(overdue)} overdue task{'s' if len(overdue) != 1 else ''}</b>\n"]
    for c in sorted(overdue, key=lambda x: x.scheduled_at):
        days = (today - c.scheduled_at.date()).days
        lines.append(f"• {c.title} ({days}d)")

    return "sent" if send_message(token, chat_id, "\n".join(lines)) else "send_failed"


def check_all(db: Session) -> dict:
    """Run all scheduled checks. Called by the main.py background scheduler."""
    s = Settings(db)
    token   = s.telegram_token
    chat_id = s.telegram_chat_id
    if not token or not chat_id:
        return {"skipped": True, "reason": "not configured"}

    tz_offset = s.tz_offset
    now_local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today     = now_local.date()

    return {
        "briefing":       check_briefing(db, token, chat_id, tz_offset, now_local, today),
        "habit_reminder": check_habit_reminder(db, token, chat_id, now_local, today),
        "overdue_nudge":  check_overdue_nudge(db, token, chat_id, now_local, today),
    }
