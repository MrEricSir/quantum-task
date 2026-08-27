"""
Registry of exportable data sections -- one entry per user-data domain, so
GET /api/export can grow (new feature package = new section) without a
single hand-maintained function enumerating every table. Mirrors the plain
central-list shape of capabilities/registry.py and model_plugins/__init__.py
(a list built here, not each feature self-registering) for the same reason:
avoids import-order surprises if a section's fetch function ever needs to
import a feature package.

To add a new domain to the export:
  1. Write a fetch(db) -> list[dict] function below -- _serialize_columns
     covers plain-column models in one line; add relationship fields (e.g.
     tag names) by hand afterward, same as _fetch_cards/_fetch_habits do.
  2. Add an ExportSection entry to _sections.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

import app_setting_keys as keys
import models
from settings import Settings


@dataclass
class ExportSection:
    name: str
    fetch: Callable[[Session], list[dict[str, Any]]]


def _json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _serialize_columns(row) -> dict:
    """Plain-column dict for a model row -- reads columns off the table
    definition itself, so a new column added to models.py is picked up
    automatically instead of silently missing from every future export."""
    return {c.name: _json_safe(getattr(row, c.name)) for c in row.__table__.columns}


def _fetch_tags(db: Session) -> list[dict]:
    return [_serialize_columns(t) for t in db.query(models.Tag).all()]


def _fetch_cards(db: Session) -> list[dict]:
    out = []
    for c in db.query(models.Card).all():
        row = _serialize_columns(c)
        row["tags"] = [t.name for t in c.tags]
        out.append(row)
    return out


def _fetch_habits(db: Session) -> list[dict]:
    out = []
    for h in db.query(models.Habit).all():
        row = _serialize_columns(h)
        row["tags"] = [t.name for t in h.tags]
        out.append(row)
    return out


def _fetch_habit_completions(db: Session) -> list[dict]:
    return [_serialize_columns(c) for c in db.query(models.HabitCompletion).all()]


def _fetch_calendar_mappings(db: Session) -> list[dict]:
    return [_serialize_columns(m) for m in db.query(models.CalendarMapping).all()]


def _fetch_mood_logs(db: Session) -> list[dict]:
    return [_serialize_columns(m) for m in db.query(models.MoodLog).all()]


def _fetch_food_entries(db: Session) -> list[dict]:
    return [_serialize_columns(f) for f in db.query(models.FoodEntry).all()]


def _fetch_workout_entries(db: Session) -> list[dict]:
    return [_serialize_columns(w) for w in db.query(models.WorkoutEntry).all()]


def _fetch_health_experiments(db: Session) -> list[dict]:
    return [_serialize_columns(e) for e in db.query(models.HealthExperiment).all()]


def _fetch_withings_measurements(db: Session) -> list[dict]:
    """Measurements only -- WithingsCredentials (access/refresh tokens) is
    deliberately never exported, see _SETTINGS_ALLOWLIST below for the same
    reasoning applied to the AppSetting table."""
    return [_serialize_columns(m) for m in db.query(models.WithingsMeasurement).all()]


# Explicit allowlist of AppSetting keys safe to include in a personal data
# export -- real user-facing configuration only. Deliberately an allowlist,
# not an exclude-list: a new secret/credential key added to
# app_setting_keys.py later (a token, a webhook secret) is excluded by
# default instead of silently leaking into an export file until someone
# remembers to blocklist it. Excludes tokens/secrets (GITHUB_TOKEN,
# TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, VAPID_PRIVATE_KEY,
# BRIDGE_INSTALL_TOKEN, EXPORT_TOKEN) and pure internal bookkeeping that
# wouldn't mean anything restored later (last-sent/dedup markers, the cached
# last-known lat/lon).
_SETTINGS_ALLOWLIST = {
    keys.DISCOVERY_INTERESTS,
    keys.GITHUB_REPOS,
    keys.GITHUB_STATUS_CONFIG,
    keys.GITHUB_REPO_TAGS,
    keys.WITHINGS_HEALTH_GOALS,
    keys.TELEGRAM_CHAT_ID,
    keys.BRIEFING_SCHEDULE_TIME,
    keys.BRIEFING_TZ_OFFSET,
    keys.HABIT_REMINDER_TIME,
    keys.OVERDUE_NUDGE_TIME,
    keys.WEEKLY_REVIEW_SCHEDULE_TIME,
    keys.NAV_ORDER,
    keys.DEFAULT_PAGE,
}


def _fetch_settings(db: Session) -> list[dict]:
    settings = Settings(db)
    out = []
    for key in sorted(_SETTINGS_ALLOWLIST):
        value = settings.get(key)
        if value:
            out.append({"key": key, "value": value})
    return out


_sections = [
    ExportSection("tags", _fetch_tags),
    ExportSection("cards", _fetch_cards),
    ExportSection("habits", _fetch_habits),
    ExportSection("habit_completions", _fetch_habit_completions),
    ExportSection("calendar_mappings", _fetch_calendar_mappings),
    ExportSection("mood_logs", _fetch_mood_logs),
    ExportSection("food_entries", _fetch_food_entries),
    ExportSection("workout_entries", _fetch_workout_entries),
    ExportSection("health_experiments", _fetch_health_experiments),
    ExportSection("withings_measurements", _fetch_withings_measurements),
    ExportSection("settings", _fetch_settings),
]

REGISTRY: dict[str, ExportSection] = {s.name: s for s in _sections}


def build_export(db: Session) -> dict[str, Any]:
    return {name: section.fetch(db) for name, section in REGISTRY.items()}
