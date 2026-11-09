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

    def get_many(self, keys_: list[str]) -> dict[str, str]:
        """Bulk-fetch several keys in one query -- for a hot path (e.g. AuthMiddleware,
        which runs this on every authenticated request) where N separate .get() calls would
        mean N round trips instead of one. Missing keys are simply absent from the result;
        callers should use dict.get(key, default) same as a single .get() would."""
        rows = self._db.query(models.AppSetting).filter(models.AppSetting.key.in_(keys_))
        return {row.key: row.value for row in rows}

    def set(self, key: str, value: str) -> None:
        row = self._db.query(models.AppSetting).filter_by(key=key).first()
        if row:
            row.value = value
        else:
            self._db.add(models.AppSetting(key=key, value=value))

    def delete(self, key: str) -> None:
        row = self._db.query(models.AppSetting).filter_by(key=key).first()
        if row:
            self._db.delete(row)

    # ── Auth ──────────────────────────────────────────────────────────────────

    @property
    def session_secret(self) -> str:
        return self.get(keys.SESSION_SECRET)

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
    def telegram_last_update_id(self) -> int:
        return int(self.get(keys.TELEGRAM_LAST_UPDATE_ID, "0") or "0")

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
    def overdue_nudge_time(self) -> str:
        return self.get(keys.OVERDUE_NUDGE_TIME)

    @property
    def overdue_nudge_last_sent(self) -> str:
        return self.get(keys.OVERDUE_NUDGE_LAST_SENT)

    @property
    def evening_summary_last_sent(self) -> str:
        return self.get(keys.EVENING_SUMMARY_LAST_SENT)

    @property
    def weekly_review_schedule_time(self) -> str:
        return self.get(keys.WEEKLY_REVIEW_SCHEDULE_TIME, "SUN:18:00")

    @property
    def weekly_review_last_sent(self) -> str:
        return self.get(keys.WEEKLY_REVIEW_LAST_SENT)

    @property
    def meeting_alerts_sent(self) -> str:
        return self.get(keys.MEETING_ALERTS_SENT, "")

    @property
    def streak_milestones_sent(self) -> str:
        return self.get(keys.STREAK_MILESTONES_SENT, "")

    @property
    def health_nudges_sent(self) -> str:
        return self.get(keys.HEALTH_NUDGES_SENT, "")

    # ── GitHub ────────────────────────────────────────────────────────────────

    @property
    def github_token(self) -> str:
        return self.get(keys.GITHUB_TOKEN)

    @property
    def github_repos(self) -> str:
        """Raw newline-separated text (one repo per line), not JSON -- see github_sync.py's
        get_config/save_config, the only real reader/writer of this key."""
        return self.get(keys.GITHUB_REPOS)

    @property
    def github_status_config(self) -> str:
        return self.get(keys.GITHUB_STATUS_CONFIG, "{}")

    @property
    def github_repo_tags(self) -> str:
        return self.get(keys.GITHUB_REPO_TAGS, "{}")

    # ── Location ──────────────────────────────────────────────────────────────

    @property
    def last_known_lat(self) -> float | None:
        v = self.get(keys.LAST_KNOWN_LAT)
        return float(v) if v else None

    @property
    def last_known_lon(self) -> float | None:
        v = self.get(keys.LAST_KNOWN_LON)
        return float(v) if v else None

    @property
    def discovery_max_distance_miles(self) -> float:
        v = self.get(keys.DISCOVERY_MAX_DISTANCE_MILES)
        return float(v) if v else 25.0

    @property
    def discovery_distance_unit(self) -> str:
        return self.get(keys.DISCOVERY_DISTANCE_UNIT, "mi")

    @property
    def discovery_interests(self) -> str:
        return self.get(keys.DISCOVERY_INTERESTS, "")

    # ── Navigation preferences ───────────────────────────────────────────────

    @property
    def nav_order(self) -> str:
        return self.get(keys.NAV_ORDER, "[]")

    @property
    def default_page(self) -> str:
        return self.get(keys.DEFAULT_PAGE, "today")

    # ── Health experiment category preferences ──────────────────────────────

    @property
    def disabled_experiment_categories(self) -> str:
        return self.get(keys.HEALTH_EXPERIMENT_DISABLED_CATEGORIES, "[]")

    # ── Web push (VAPID) ──────────────────────────────────────────────────────

    @property
    def vapid_private_key(self) -> str:
        return self.get(keys.VAPID_PRIVATE_KEY)

    @property
    def vapid_public_key(self) -> str:
        return self.get(keys.VAPID_PUBLIC_KEY)

    # ── Bridge (qtask-bridge) ─────────────────────────────────────────────────

    @property
    def bridge_install_token(self) -> str:
        return self.get(keys.BRIDGE_INSTALL_TOKEN)

    @property
    def bridge_token(self) -> str:
        return self.get(keys.BRIDGE_TOKEN)

    @property
    def export_token(self) -> str:
        return self.get(keys.EXPORT_TOKEN)

    # ── Withings health goals ─────────────────────────────────────────────────

    @property
    def health_goals(self) -> str:
        """Raw JSON string, same convention as nav_order -- caller parses it."""
        return self.get(keys.WITHINGS_HEALTH_GOALS, "{}")
