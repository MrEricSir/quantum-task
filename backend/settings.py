"""
Typed settings layer over the AppSetting key-value table.

Usage:
    from settings import Settings
    s = Settings(db)
    token = s.telegram_token
    s.set(keys.BRIEFING_LAST_SENT, today.isoformat())

This is the single implementation of get/set for AppSetting rows.
Routers should use Settings(db) rather than inline _get/_set helpers.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

import app_setting_keys as keys
import models


class Settings:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ── Core accessors ────────────────────────────────────────────────────────

    def get(self, key: str, default: str = "") -> str:
        row = self._db.query(models.AppSetting).filter_by(key=key).first()
        return row.value if row and row.value else default

    def set(self, key: str, value: str) -> None:
        row = self._db.query(models.AppSetting).filter_by(key=key).first()
        if row:
            row.value = value
        else:
            self._db.add(models.AppSetting(key=key, value=value))

    # ── Telegram ──────────────────────────────────────────────────────────────

    @property
    def telegram_token(self) -> str:
        return self.get(keys.TELEGRAM_BOT_TOKEN)

    @property
    def telegram_chat_id(self) -> str:
        return self.get(keys.TELEGRAM_CHAT_ID)

    @property
    def telegram_webhook_secret(self) -> str:
        return self.get(keys.TELEGRAM_WEBHOOK_SECRET)

    @property
    def briefing_schedule_time(self) -> str:
        return self.get(keys.BRIEFING_SCHEDULE_TIME, "07:30")

    @property
    def tz_offset(self) -> int:
        return int(self.get(keys.BRIEFING_TZ_OFFSET, "0") or "0")

    @property
    def briefing_last_sent(self) -> str:
        return self.get(keys.BRIEFING_LAST_SENT)

    @property
    def habit_reminder_time(self) -> str:
        return self.get(keys.HABIT_REMINDER_TIME)

    @property
    def habit_reminder_last_sent(self) -> str:
        return self.get(keys.HABIT_REMINDER_LAST_SENT)

    @property
    def overdue_nudge_time(self) -> str:
        return self.get(keys.OVERDUE_NUDGE_TIME)

    @property
    def overdue_nudge_last_sent(self) -> str:
        return self.get(keys.OVERDUE_NUDGE_LAST_SENT)

    @property
    def evening_summary_last_sent(self) -> str:
        return self.get(keys.EVENING_SUMMARY_LAST_SENT)

    @property
    def meeting_alerts_sent(self) -> str:
        return self.get(keys.MEETING_ALERTS_SENT, "")

    @property
    def streak_milestones_sent(self) -> str:
        return self.get(keys.STREAK_MILESTONES_SENT, "")

    # ── GitHub ────────────────────────────────────────────────────────────────

    @property
    def github_token(self) -> str:
        return self.get(keys.GITHUB_TOKEN)

    @property
    def github_repos(self) -> str:
        return self.get(keys.GITHUB_REPOS, "[]")

    # ── Location ──────────────────────────────────────────────────────────────

    @property
    def last_known_lat(self) -> float | None:
        v = self.get(keys.LAST_KNOWN_LAT)
        return float(v) if v else None

    @property
    def last_known_lon(self) -> float | None:
        v = self.get(keys.LAST_KNOWN_LON)
        return float(v) if v else None
