"""
Telegram scheduled notification checks.

Called every minute by the background task in main.py via check_all().
Each function is idempotent — it checks whether its condition is met and
whether it has already fired today before sending anything.
"""
import json as _json
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


def check_evening_summary(db: Session, token: str, chat_id: str,
                           tz_offset: int, now_local: datetime, today) -> str:
    """Send an evening summary: tasks done, habits status, and tomorrow preview."""
    s = Settings(db)
    if not _hour_matches(s.habit_reminder_time, now_local):
        return "skipped"
    if s.evening_summary_last_sent == today.isoformat():
        return "already_sent"

    today_str = today.isoformat()
    tomorrow = today + timedelta(days=1)

    # Tasks completed today (local date)
    completed_today = [
        c for c in db.query(models.Card).filter(
            models.Card.completed == True,   # noqa: E712
            models.Card.archived == False,   # noqa: E712
            models.Card.completed_at.isnot(None),
        ).all()
        if (c.completed_at.replace(tzinfo=None) - timedelta(minutes=tz_offset)).date() == today
    ]

    # Tasks still pending in today's board
    pending_cards = (
        db.query(models.Card)
        .filter_by(section="today", completed=False, archived=False)
        .order_by(models.Card.position)
        .all()
    )

    # Habits
    habits = db.query(models.Habit).filter_by(archived=False).all()
    completed_habit_ids = {
        r.habit_id for r in db.query(models.HabitCompletion).filter_by(date=today_str).all()
    }

    # Tomorrow's scheduled tasks
    tomorrow_cards = [
        c for c in db.query(models.Card).filter(
            models.Card.completed == False,   # noqa: E712
            models.Card.archived == False,    # noqa: E712
            models.Card.scheduled_at.isnot(None),
        ).all()
        if c.scheduled_at.date() == tomorrow
    ]

    s.set(keys.EVENING_SUMMARY_LAST_SENT, today_str)
    db.commit()

    lines = [f"<b>📊 Evening wrap-up — {today.strftime('%A, %b %-d')}</b>"]

    if completed_today:
        lines.append(f"\n<b>✅ {len(completed_today)} task{'s' if len(completed_today) != 1 else ''} done</b>")
        for c in completed_today[:6]:
            lines.append(f"  ✓ {c.title}")
    else:
        lines.append("\n<i>No tasks completed today.</i>")

    if habits:
        done_habits = [h for h in habits if h.id in completed_habit_ids]
        pending_habits = [h for h in habits if h.id not in completed_habit_ids]
        lines.append(f"\n<b>🔁 Habits: {len(done_habits)}/{len(habits)} done</b>")
        for h in done_habits:
            lines.append(f"  ✓ {h.name}")
        for h in pending_habits:
            lines.append(f"  ○ {h.name}")

    if pending_cards:
        lines.append(f"\n<b>📋 {len(pending_cards)} carrying over</b>")
        for c in pending_cards[:5]:
            lines.append(f"  • {c.title}")
        if len(pending_cards) > 5:
            lines.append(f"  … and {len(pending_cards) - 5} more")

    if tomorrow_cards:
        lines.append(f"\n<b>📅 Tomorrow</b>")
        for c in sorted(tomorrow_cards, key=lambda x: x.scheduled_at)[:4]:
            lines.append(f"  • {c.title} @ {c.scheduled_at.strftime('%-I:%M %p')}")

    return "sent" if send_message(token, chat_id, "\n".join(lines)) else "send_failed"


def check_meeting_alerts(db: Session, token: str, chat_id: str,
                          tz_offset: int, now_utc: datetime, now_local: datetime) -> str:
    """Alert for calendar meetings starting in ~30 minutes (25–35 min window)."""
    from gcal import _cached_fetch_events

    s = Settings(db)
    today = now_local.date()

    # Load already-alerted event IDs for today
    try:
        stored = _json.loads(s.meeting_alerts_sent) if s.meeting_alerts_sent else {}
    except Exception:
        stored = {}
    if stored.get("date") != today.isoformat():
        stored = {"date": today.isoformat(), "ids": []}
    alerted_ids = set(stored["ids"])

    # Window: 25–35 minutes from now (UTC)
    window_start = now_utc + timedelta(minutes=25)
    window_end   = now_utc + timedelta(minutes=35)

    mappings = db.query(models.CalendarMapping).all()
    to_alert = []
    for m in mappings:
        try:
            for ev in _cached_fetch_events(m.ical_url, today, today + timedelta(days=1)):
                if ev.get("is_ooo") or ev.get("all_day"):
                    continue
                ev_id = str(ev["id"])
                if ev_id in alerted_ids:
                    continue
                start = ev["start"]
                start_naive = start.replace(tzinfo=None) if start.tzinfo else start
                if window_start.replace(tzinfo=None) <= start_naive <= window_end.replace(tzinfo=None):
                    # Compute local display time
                    start_local = start_naive - timedelta(minutes=tz_offset)
                    mins_away = int((start_naive - now_utc.replace(tzinfo=None)).total_seconds() / 60)
                    to_alert.append((ev_id, ev["title"], start_local, mins_away))
        except Exception as e:
            print(f"[telegram] meeting alert error for mapping {m.id}: {e}")

    if not to_alert:
        return "skipped: no meetings in window"

    # Persist alerted IDs before sending
    for ev_id, _, _, _ in to_alert:
        alerted_ids.add(ev_id)
    stored["ids"] = list(alerted_ids)
    s.set(keys.MEETING_ALERTS_SENT, _json.dumps(stored))
    db.commit()

    sent = 0
    for _, title, start_local, mins_away in to_alert:
        text = f"📅 <b>{title}</b> in {mins_away} min ({start_local.strftime('%-I:%M %p')})"
        if send_message(token, chat_id, text):
            sent += 1

    return f"sent: {sent} alert(s)"


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


_STREAK_MILESTONES = [3, 7, 14, 21, 30, 60, 100, 365]


def check_streak_milestones(db: Session, token: str, chat_id: str,
                              now_local: datetime, today) -> str:
    """Send a celebration message when a habit streak crosses a milestone."""
    from streak import get_current_streak

    s = Settings(db)
    try:
        sent_map: dict = _json.loads(s.streak_milestones_sent) if s.streak_milestones_sent else {}
    except Exception:
        sent_map = {}

    habits = db.query(models.Habit).filter_by(archived=False).all()
    alerts = []

    for h in habits:
        streak = get_current_streak(db, h.id, today)
        if streak not in _STREAK_MILESTONES:
            continue
        key = f"{h.id}:{streak}"
        if sent_map.get(key) == today.isoformat():
            continue  # already sent today
        alerts.append((h.name, streak))
        sent_map[key] = today.isoformat()

    if not alerts:
        return "skipped: no milestones"

    s.set(keys.STREAK_MILESTONES_SENT, _json.dumps(sent_map))
    db.commit()

    sent = 0
    for name, days in alerts:
        if days >= 100:
            medal = "🏆"
        elif days >= 30:
            medal = "🥇"
        elif days >= 14:
            medal = "🥈"
        else:
            medal = "🔥"
        text = f"{medal} <b>{days}-day {name} streak!</b> Keep it going."
        if send_message(token, chat_id, text):
            sent += 1

    return f"sent: {sent} milestone(s)"


_OUTPUT_TAIL_LINES = 10  # lines of Claude Code output included in completion notifications


def _tail(text: str, n: int) -> str:
    """Return the last n lines of text, or all of it if shorter."""
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def check_bridge_jobs(db: Session, token: str, chat_id: str) -> str:
    """Notify about bridge job starts and completions (once per job, per event)."""
    s = Settings(db)
    sent = 0

    # ── "Started" notifications ────────────────────────────────────────────────
    try:
        last_started = int(s.get(keys.BRIDGE_LAST_NOTIFIED_RUNNING_JOB, "0"))
    except (ValueError, TypeError):
        last_started = 0

    started_jobs = (
        db.query(models.BridgeJob)
        .filter(
            models.BridgeJob.id > last_started,
            models.BridgeJob.status.in_(["running", "done", "error"]),
        )
        .order_by(models.BridgeJob.id)
        .all()
    )

    for job in started_jobs:
        card = db.query(models.Card).filter_by(id=job.card_id).first()
        card_title = card.title if card else f"card #{job.card_id}"
        # Only send "started" if the job is still running — if it's already done/error
        # the completion notification below covers it; no need for two messages.
        if job.status == "running":
            msg = f'▶ Claude Code started on <b>{card_title}</b>'
            if send_message(token, chat_id, msg):
                sent += 1

    if started_jobs:
        s.set(keys.BRIDGE_LAST_NOTIFIED_RUNNING_JOB, str(started_jobs[-1].id))

    # ── Completion notifications ───────────────────────────────────────────────
    try:
        last_notified = int(s.get(keys.BRIDGE_LAST_NOTIFIED_JOB, "0"))
    except (ValueError, TypeError):
        last_notified = 0

    finished_jobs = (
        db.query(models.BridgeJob)
        .filter(
            models.BridgeJob.id > last_notified,
            models.BridgeJob.status.in_(["done", "error"]),
        )
        .order_by(models.BridgeJob.id)
        .all()
    )

    for job in finished_jobs:
        card = db.query(models.Card).filter_by(id=job.card_id).first()
        card_title = card.title if card else f"card #{job.card_id}"
        if job.status == "done":
            msg = f'✅ Build complete: <b>{card_title}</b>'
        else:
            msg = f'❌ Build failed: <b>{card_title}</b>'
        if job.branch_name:
            suffix = f' ({job.agent_name})' if job.agent_name else ''
            msg += f'\n<code>{job.branch_name}</code>{suffix}'
        if job.result:
            msg += f'\n{job.result}'
        tail = _tail(job.output, _OUTPUT_TAIL_LINES)
        if tail:
            msg += f'\n\n<pre>{tail}</pre>'
        if send_message(token, chat_id, msg):
            sent += 1

    if finished_jobs:
        s.set(keys.BRIDGE_LAST_NOTIFIED_JOB, str(finished_jobs[-1].id))

    db.commit()
    return f"notified: {sent} event(s)" if sent else "none"


def check_all(db: Session) -> dict:
    """Run all scheduled checks. Called by the main.py background scheduler."""
    s = Settings(db)
    token   = s.telegram_token
    chat_id = s.telegram_chat_id
    if not token or not chat_id:
        return {"skipped": True, "reason": "not configured"}

    tz_offset = s.tz_offset
    now_utc   = datetime.now(timezone.utc)
    now_local = now_utc.replace(tzinfo=None) - timedelta(minutes=tz_offset)
    today     = now_local.date()

    return {
        "briefing":           check_briefing(db, token, chat_id, tz_offset, now_local, today),
        "evening_summary":    check_evening_summary(db, token, chat_id, tz_offset, now_local, today),
        "overdue_nudge":      check_overdue_nudge(db, token, chat_id, now_local, today),
        "meeting_alerts":     check_meeting_alerts(db, token, chat_id, tz_offset, now_utc, now_local),
        "streak_milestones":  check_streak_milestones(db, token, chat_id, now_local, today),
        "bridge_jobs":        check_bridge_jobs(db, token, chat_id),
    }
