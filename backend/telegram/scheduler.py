"""
Telegram scheduled notification checks.

check_all() is called from POST /api/telegram/daily-briefing, hit hourly by
a Cloud Scheduler job in prod (see dev.sh's gcp_setup_scheduler()) since
Cloud Run runs with min instances 0 and has no reliable in-process loop.
Each function is idempotent — it checks whether its condition is met and
whether it has already fired today before sending anything.
"""
import json as _json
from datetime import date, datetime, timezone, timedelta

from sqlalchemy.orm import Session

import app_setting_keys as keys
import models
from bridge.stale import STALE_THRESHOLD_MINUTES
from database import SessionLocal
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

    # Tasks completed today (local date). Deliberately not filtered on
    # archived -- a GitHub-linked card gets completed AND archived in the
    # same step when its issue/PR closes (github_sync.py), so requiring
    # archived == False here silently dropped every GitHub-ticket task from
    # the summary on the day it closed. Matches generate_weekly_review's own
    # completed-task count and _day_has_completed_task below, neither of
    # which filter on archived either.
    completed_today = [
        c for c in db.query(models.Card).filter(
            models.Card.completed == True,   # noqa: E712
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


_WEEKDAY_ABBR = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]  # datetime.weekday(): Monday=0


def _day_and_hour_match(spec: str, now_local: datetime) -> bool:
    """spec is 'DOW:HH:MM', e.g. 'SUN:18:00' -- day must match exactly, hour
    reuses _hour_matches (minute granularity isn't checked anywhere in this
    file since check_all() only runs hourly)."""
    if not spec or spec.count(":") != 2:
        return False
    dow, hh, mm = spec.split(":")
    if _WEEKDAY_ABBR[now_local.weekday()] != dow.strip().upper():
        return False
    return _hour_matches(f"{hh}:{mm}", now_local)


def _isoweek_str(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def generate_weekly_review(today: date, tz_offset: int) -> str | None:
    """Build the weekly review message text: tasks completed, per-habit
    completion rate + current streak, this week's dismissed health experiment
    outcome (if any), and the correlation summary already computed for the
    Health page -- reused from routers/correlations.py rather than
    recomputed. Trailing 7 days ending today, not aligned to any particular
    weekday, so the content window stays correct regardless of what day the
    review itself is configured to send on.

    Factored out standalone (mirrors briefing/generate.py's
    generate_today_briefing) so both check_weekly_review below AND the
    ungated POST /api/telegram/test-weekly-review debug endpoint call the
    exact same content logic. Returns None on failure so callers can tell
    "nothing to report" apart from "generation broke"."""
    from streak import get_current_streak
    from deps import llm_client, LLM_MODEL
    from routers.correlations import _load_weekly_obs, _compute_correlations, _llm_summary

    week_start = today - timedelta(days=6)

    try:
        with SessionLocal() as db:
            recent_completed = db.query(models.Card).filter(
                models.Card.completed == True,  # noqa: E712
                models.Card.completed_at.isnot(None),
            ).all()
            completed_count = sum(
                1 for c in recent_completed
                if week_start <= (c.completed_at.replace(tzinfo=None) - timedelta(minutes=tz_offset)).date() <= today
            )

            habits = db.query(models.Habit).filter_by(archived=False).order_by(models.Habit.id).all()
            habit_lines = []
            for h in habits:
                n = db.query(models.HabitCompletion).filter(
                    models.HabitCompletion.habit_id == h.id,
                    models.HabitCompletion.date >= week_start.isoformat(),
                    models.HabitCompletion.date <= today.isoformat(),
                ).count()
                streak = get_current_streak(db, h.id, today)
                streak_note = f", {streak}-day streak" if streak >= 2 else ""
                habit_lines.append(f"{h.name}: {n}/7 days ({round(n / 7 * 100)}%){streak_note}")

            recent_experiments = (
                db.query(models.HealthExperiment)
                .filter(
                    models.HealthExperiment.status == "dismissed",
                    models.HealthExperiment.dismissed_at.isnot(None),
                )
                .order_by(models.HealthExperiment.dismissed_at.desc())
                .limit(5)
                .all()
            )
            experiment = next(
                (e for e in recent_experiments if week_start <= (
                    e.dismissed_at.replace(tzinfo=None) - timedelta(minutes=tz_offset)
                ).date() <= today),
                None,
            )

            weight_obs, fat_obs = _load_weekly_obs(db, today)
            corr_summary = None
            if weight_obs or fat_obs:
                correlations = _compute_correlations(weight_obs, fat_obs)
                if correlations:
                    corr_summary = _llm_summary(correlations)

        ctx_lines = [f"Week of {week_start.strftime('%b %-d')} - {today.strftime('%b %-d, %Y')}"]
        ctx_lines.append(f"Tasks completed: {completed_count}")
        if habit_lines:
            ctx_lines.append("Habits:")
            ctx_lines.extend(f"  - {line}" for line in habit_lines)
        if experiment:
            exp_line = f"This week's health experiment: {experiment.text}"
            if experiment.workout_p is not None:
                sig = "a statistically significant change" if experiment.workout_p < 0.05 else "no statistically significant change"
                exp_line += f" -- result: {sig} (p={experiment.workout_p:.3f})"
            elif experiment.weight_delta is not None and experiment.weight_baseline is not None:
                diff = experiment.weight_delta - experiment.weight_baseline
                trend = "improved" if diff < 0 else "worsened" if diff > 0 else "held steady"
                exp_line += f" -- weight trend {trend} vs baseline"
            ctx_lines.append(exp_line)
        if corr_summary:
            ctx_lines.append(f"Correlation analysis: {corr_summary}")

        system = (
            "You write a short, warm weekly review message for a personal productivity "
            "and health app, sent over Telegram. Summarize the week's data given below in "
            "3-5 short sentences -- highlight genuine wins, note anything that slipped, and "
            "end with one specific, encouraging suggestion for next week if the data "
            "supports one. Do not invent any numbers not given below. No lists, no "
            "headers, just warm, direct prose."
        )
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(ctx_lines)},
            ],
            timeout=20,
            temperature=0.4,
            # See correlations.py's _generate_experiment for the full story: on a reasoning
            # model, an unbounded chain-of-thought adds real latency even with no max_tokens
            # cap here to truncate against.
            reasoning_effort="low",
        )
        body = resp.choices[0].message.content.strip()
        header = f"<b>📆 Weekly review — {week_start.strftime('%b %-d')} to {today.strftime('%b %-d')}</b>\n\n"
        return header + body
    except Exception as e:
        print(f"[telegram] weekly review generation error: {e}")
        return None


def check_weekly_review(db: Session, token: str, chat_id: str,
                         tz_offset: int, now_local: datetime, today: date) -> str:
    """Send the weekly review if it's the configured day+hour and hasn't
    already gone out this ISO week."""
    s = Settings(db)
    if not _day_and_hour_match(s.weekly_review_schedule_time, now_local):
        return "skipped"
    current_week = _isoweek_str(today)
    if s.weekly_review_last_sent == current_week:
        return "already_sent"

    s.set(keys.WEEKLY_REVIEW_LAST_SENT, current_week)
    db.commit()

    text = generate_weekly_review(today, tz_offset)
    if not text:
        return "error: generation failed"

    return "sent" if send_message(token, chat_id, text) else "send_failed"


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

_FOOD_QUALITY_STREAK_THRESHOLD = 7  # avg daily food quality >= this counts as a "good" day


def _day_food_quality_ok(db: Session, day) -> bool:
    day_str = day.isoformat()
    next_day_str = (day + timedelta(days=1)).isoformat()
    qualities = [
        q for (q,) in db.query(models.FoodEntry.quality).filter(
            models.FoodEntry.consumed_at >= day_str,
            models.FoodEntry.consumed_at < next_day_str,
            models.FoodEntry.quality.isnot(None),
        ).all()
    ]
    if not qualities:
        return False
    return (sum(qualities) / len(qualities)) >= _FOOD_QUALITY_STREAK_THRESHOLD


def _day_has_completed_task(db: Session, day) -> bool:
    day_str = day.isoformat()
    next_day_str = (day + timedelta(days=1)).isoformat()
    return db.query(models.Card).filter(
        models.Card.completed == True,  # noqa: E712
        models.Card.completed_at >= day_str,
        models.Card.completed_at < next_day_str,
    ).first() is not None


def _consecutive_streak_ending(today, day_ok_fn, max_days=1000) -> int:
    """Count consecutive qualifying days ending at `today`, falling back to
    ending at yesterday if today doesn't qualify yet — a streak is still
    "alive" until the day is over, mirroring habit streak semantics."""
    end = today if day_ok_fn(today) else today - timedelta(days=1)
    count = 0
    day = end
    while day_ok_fn(day):
        count += 1
        day -= timedelta(days=1)
        if count >= max_days:
            break
    return count


def check_streak_milestones(db: Session, token: str, chat_id: str,
                              now_local: datetime, today) -> str:
    """Send a celebration message when a streak crosses a milestone: habit
    streaks, a food-quality streak, and a task-completion streak all share
    the same milestone list and message format. A habit milestone also notes
    when it's a new personal best (tracked via a per-habit watermark, so this
    only ever fires at the same sparse milestone cadence, never daily)."""
    from streak import get_current_streak

    s = Settings(db)
    try:
        sent_map: dict = _json.loads(s.streak_milestones_sent) if s.streak_milestones_sent else {}
    except Exception:
        sent_map = {}

    habits = db.query(models.Habit).filter_by(archived=False).all()
    alerts = []  # (label, days, is_personal_best)

    for h in habits:
        streak = get_current_streak(db, h.id, today)
        if streak not in _STREAK_MILESTONES:
            continue
        key = f"{h.id}:{streak}"
        if sent_map.get(key) == today.isoformat():
            continue  # already sent today

        best_key = f"habit_best:{h.id}"
        try:
            prev_best = int(sent_map.get(best_key, "0") or "0")
        except (TypeError, ValueError):
            prev_best = 0
        is_record = streak > prev_best
        if is_record:
            sent_map[best_key] = str(streak)

        alerts.append((h.name, streak, is_record))
        sent_map[key] = today.isoformat()

    food_streak = _consecutive_streak_ending(today, lambda d: _day_food_quality_ok(db, d))
    if food_streak in _STREAK_MILESTONES:
        key = f"food_quality:{food_streak}"
        if sent_map.get(key) != today.isoformat():
            alerts.append(("food quality", food_streak, False))
            sent_map[key] = today.isoformat()

    task_streak = _consecutive_streak_ending(today, lambda d: _day_has_completed_task(db, d))
    if task_streak in _STREAK_MILESTONES:
        key = f"task_completion:{task_streak}"
        if sent_map.get(key) != today.isoformat():
            alerts.append(("task completion", task_streak, False))
            sent_map[key] = today.isoformat()

    if not alerts:
        return "skipped: no milestones"

    s.set(keys.STREAK_MILESTONES_SENT, _json.dumps(sent_map))
    db.commit()

    sent = 0
    for name, days, is_record in alerts:
        if days >= 100:
            medal = "🏆"
        elif days >= 30:
            medal = "🥇"
        elif days >= 14:
            medal = "🥈"
        else:
            medal = "🔥"
        text = f"{medal} <b>{days}-day {name} streak!</b> Keep it going."
        if is_record:
            text += "\n🎉 New personal best!"
        if send_message(token, chat_id, text):
            sent += 1

    return f"sent: {sent} milestone(s)"


# ── Proactive health/habit nudges ───────────────────────────────────────────────
# High-signal, pattern-based flags only — never a single missed day. Each signal
# has its own cooldown (tracked in HEALTH_NUDGES_SENT) so a persistent issue is
# flagged once and then left alone for a while, instead of nagging daily. All
# signals that fire in a given run are bundled into one message.

_STREAK_RISK_MIN_STREAK = 3        # only worth protecting once it's a real streak
_STREAK_RISK_COOLDOWN_DAYS = 1     # inherently a same-day signal

_GOING_COLD_WINDOW_DAYS = 7
_GOING_COLD_THRESHOLD = 0.5        # completion rate below this over the window
_GOING_COLD_MIN_HABIT_AGE_DAYS = 7  # don't flag a habit before it has a track record
_GOING_COLD_COOLDOWN_DAYS = 7

_FOOD_LOG_QUIET_DAYS = 2           # consecutive days (including today) with zero entries
_FOOD_LOG_QUIET_COOLDOWN_DAYS = 3

_WITHINGS_DRIFT_WINDOW_DAYS = 7
_WITHINGS_DRIFT_MIN_READINGS = 3   # need enough recent syncs to trust the average
_WITHINGS_STEPS_DRIFT_RATIO = 0.8  # average below 80% of goal counts as drift
_WITHINGS_DRIFT_COOLDOWN_DAYS = 7


def _load_nudge_state(s: Settings) -> dict:
    try:
        return _json.loads(s.health_nudges_sent) if s.health_nudges_sent else {}
    except Exception:
        return {}


def _cooldown_ok(state: dict, key: str, today, cooldown_days: int) -> bool:
    """True if `key` hasn't fired within its cooldown window (or has never fired)."""
    last = state.get(key)
    if not last:
        return True
    try:
        last_date = date.fromisoformat(last)
    except ValueError:
        return True
    return (today - last_date).days >= cooldown_days


def _streak_risk_signals(db: Session, today) -> list[tuple[str, str]]:
    """Habits with a meaningful streak not yet completed today."""
    from streak import get_current_streak

    completed_ids = {
        r.habit_id for r in
        db.query(models.HabitCompletion).filter_by(date=today.isoformat()).all()
    }
    signals = []
    for h in db.query(models.Habit).filter_by(archived=False).all():
        if h.id in completed_ids:
            continue
        # Not completed today, so this returns the streak that breaks at midnight.
        streak = get_current_streak(db, h.id, today)
        if streak >= _STREAK_RISK_MIN_STREAK:
            signals.append((f"streak_risk:{h.id}", f"{h.name} ({streak}-day streak)"))
    return signals


def _going_cold_signals(db: Session, today) -> list[tuple[str, str]]:
    """Habits with a low completion rate over the trailing window, regardless
    of streak status — catches a slipping pattern with no streak left to lose."""
    age_cutoff = today - timedelta(days=_GOING_COLD_MIN_HABIT_AGE_DAYS)
    window_start = (today - timedelta(days=_GOING_COLD_WINDOW_DAYS - 1)).isoformat()
    today_str = today.isoformat()

    signals = []
    for h in db.query(models.Habit).filter_by(archived=False).all():
        if h.created_at.date() > age_cutoff:
            continue
        count = db.query(models.HabitCompletion).filter(
            models.HabitCompletion.habit_id == h.id,
            models.HabitCompletion.date >= window_start,
            models.HabitCompletion.date <= today_str,
        ).count()
        if (count / _GOING_COLD_WINDOW_DAYS) < _GOING_COLD_THRESHOLD:
            signals.append((
                f"going_cold:{h.id}",
                f"{h.name} ({count}/{_GOING_COLD_WINDOW_DAYS} days this week)",
            ))
    return signals


def _food_log_quiet_message(db: Session, today) -> str | None:
    """None if food was logged on any of the last _FOOD_LOG_QUIET_DAYS days,
    otherwise a message describing the gap."""
    for i in range(_FOOD_LOG_QUIET_DAYS):
        day = today - timedelta(days=i)
        day_str = day.isoformat()
        next_day_str = (day + timedelta(days=1)).isoformat()
        exists = db.query(models.FoodEntry).filter(
            models.FoodEntry.consumed_at >= day_str,
            models.FoodEntry.consumed_at < next_day_str,
        ).first()
        if exists:
            return None
    return f"No food logged in {_FOOD_LOG_QUIET_DAYS} days"


def _withings_drift_signals(db: Session, today) -> list[tuple[str, str]]:
    """Habits with a Withings goal whose trailing-window average has drifted
    away from the goal (steps trending down, fat ratio trending up)."""
    window_start = (today - timedelta(days=_WITHINGS_DRIFT_WINDOW_DAYS - 1)).isoformat()
    today_str = today.isoformat()

    signals = []
    habits = db.query(models.Habit).filter(
        models.Habit.archived == False,  # noqa: E712
        models.Habit.health_metric.isnot(None),
        models.Habit.health_goal.isnot(None),
    ).all()
    for h in habits:
        readings = db.query(models.WithingsMeasurement).filter(
            models.WithingsMeasurement.metric == h.health_metric,
            models.WithingsMeasurement.date >= window_start,
            models.WithingsMeasurement.date <= today_str,
        ).all()
        if len(readings) < _WITHINGS_DRIFT_MIN_READINGS:
            continue
        avg = sum(r.value for r in readings) / len(readings)
        key = f"withings_drift:{h.id}"
        if h.health_metric == "steps" and avg < h.health_goal * _WITHINGS_STEPS_DRIFT_RATIO:
            signals.append((key, f"Steps averaging {int(avg):,}/day vs {int(h.health_goal):,} goal"))
        elif h.health_metric == "fat_ratio" and avg > h.health_goal:
            signals.append((key, f"Body fat averaging {avg:.1f}% vs {h.health_goal:.1f}% goal"))
    return signals


def check_health_nudges(db: Session, token: str, chat_id: str,
                         now_local: datetime, today) -> str:
    """Bundle high-signal habit/health nudges into a single evening message:
    streak-at-risk, habits going cold, a quiet food log, and Withings goal
    drift. Runs at the same evening hour as the habit reminder. Deliberately
    does not send anything for a single missed day — only sustained patterns,
    to avoid becoming noise."""
    s = Settings(db)
    if not _hour_matches(s.habit_reminder_time, now_local):
        return "skipped"

    state = _load_nudge_state(s)
    lines = []
    fired_keys = []

    streak_risk = [(k, m) for k, m in _streak_risk_signals(db, today)
                   if _cooldown_ok(state, k, today, _STREAK_RISK_COOLDOWN_DAYS)]
    if streak_risk:
        lines.append("<b>🔥 Streak at risk today</b>")
        for k, msg in streak_risk:
            lines.append(f"  • {msg}")
            fired_keys.append(k)

    going_cold = [(k, m) for k, m in _going_cold_signals(db, today)
                  if _cooldown_ok(state, k, today, _GOING_COLD_COOLDOWN_DAYS)]
    if going_cold:
        lines.append("<b>📉 Slipping</b>")
        for k, msg in going_cold:
            lines.append(f"  • {msg}")
            fired_keys.append(k)

    if _cooldown_ok(state, "food_log_quiet", today, _FOOD_LOG_QUIET_COOLDOWN_DAYS):
        quiet_msg = _food_log_quiet_message(db, today)
        if quiet_msg:
            lines.append(f"<b>🍽 {quiet_msg}</b>")
            fired_keys.append("food_log_quiet")

    drift = [(k, m) for k, m in _withings_drift_signals(db, today)
             if _cooldown_ok(state, k, today, _WITHINGS_DRIFT_COOLDOWN_DAYS)]
    if drift:
        lines.append("<b>📊 Trending off goal</b>")
        for k, msg in drift:
            lines.append(f"  • {msg}")
            fired_keys.append(k)

    if not lines:
        return "skipped: nothing to flag"

    for k in fired_keys:
        state[k] = today.isoformat()
    s.set(keys.HEALTH_NUDGES_SENT, _json.dumps(state))
    db.commit()

    text = "<b>⚡ Health check-in</b>\n\n" + "\n".join(lines)
    return "sent" if send_message(token, chat_id, text) else "send_failed"


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
        if job.worktree_path:
            msg += f'\n<code>{job.worktree_path}</code>'
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


def notify_stalled_jobs(db: Session, token: str, chat_id: str, jobs: list) -> int:
    """Send one Telegram message per newly-stalled bridge job. Called from
    bridge.router's check-stale endpoint (not check_all()) so the DB
    transition in bridge.stale.check_stale_bridge_jobs happens regardless
    of whether Telegram is configured — this only covers the notification,
    not the state mutation. Takes the caller's own session (same pattern as
    check_bridge_jobs above) rather than opening a new one. Returns the
    number of messages actually sent."""
    sent = 0
    for job in jobs:
        card = db.query(models.Card).filter_by(id=job.card_id).first()
        card_title = card.title if card else f"card #{job.card_id}"
        msg = f'⚠ Agent went quiet: <b>{card_title}</b>'
        if job.branch_name:
            suffix = f' ({job.agent_name})' if job.agent_name else ''
            msg += f'\n<code>{job.branch_name}</code>{suffix}'
        if job.worktree_path:
            msg += f'\n<code>{job.worktree_path}</code>'
        msg += f'\nNo heartbeat for over {STALE_THRESHOLD_MINUTES} minutes — it may have crashed, lost network, or the machine went to sleep.'
        if send_message(token, chat_id, msg):
            sent += 1
    return sent


def notify_withings_reauth_needed(token: str, chat_id: str) -> bool:
    """Send a one-time Telegram notification when Withings sync hits a hard
    auth failure (invalid/revoked refresh token — reconnecting is the only
    fix). Called from routers.withings.do_sync, which owns the
    once-per-failure dedup (app_setting_keys.WITHINGS_AUTH_FAILURE_NOTIFIED)
    so this function itself doesn't need to know about that state — it just
    sends. Returns whether the message was actually sent."""
    msg = (
        "⚠ Withings needs reconnecting — sync has been failing with an "
        "invalid/expired token. Open Settings → Withings and reconnect."
    )
    return send_message(token, chat_id, msg)


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
        "weekly_review":      check_weekly_review(db, token, chat_id, tz_offset, now_local, today),
        "overdue_nudge":      check_overdue_nudge(db, token, chat_id, now_local, today),
        "meeting_alerts":     check_meeting_alerts(db, token, chat_id, tz_offset, now_utc, now_local),
        "streak_milestones":  check_streak_milestones(db, token, chat_id, now_local, today),
        "health_nudges":      check_health_nudges(db, token, chat_id, now_local, today),
        "bridge_jobs":        check_bridge_jobs(db, token, chat_id),
    }
