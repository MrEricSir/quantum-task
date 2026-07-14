"""
Constants for all keys stored in the ``app_settings`` table.

Every key used anywhere in the codebase should be listed here with a comment
explaining its value format and purpose.
"""

# ── Event discovery ────────────────────────────────────────────────────────────
# Plain text describing the user's interests, used by the LLM event ranker.
DISCOVERY_INTERESTS = "event_discovery_interests"

# ── Calendar export ────────────────────────────────────────────────────────────
# Random token embedded in the public iCal export URL (unauthenticated access).
EXPORT_TOKEN = "export_token"

# ── VAPID keys for Web Push notifications ─────────────────────────────────────
# PEM-encoded EC private key used to sign push notification requests.
VAPID_PRIVATE_KEY = "vapid_private_key"
# URL-safe base64-encoded uncompressed EC public key sent to the browser on subscription.
VAPID_PUBLIC_KEY = "vapid_public_key"

# ── GitHub integration ─────────────────────────────────────────────────────────
# GitHub personal access token with repo/issues read scope.
GITHUB_TOKEN = "github_token"
# JSON array of "owner/repo" strings to sync; empty array means all accessible repos.
GITHUB_REPOS = "github_repos"

# ── Withings health goals (still in AppSetting — not yet migrated) ─────────────
# JSON object {"steps": N|null, "fat_ratio": N|null, "weight": N|null}.
WITHINGS_HEALTH_GOALS = "withings_health_goals"

# ── Telegram briefing ──────────────────────────────────────────────────────────
# Telegram Bot API token (from @BotFather).
TELEGRAM_BOT_TOKEN = "telegram_bot_token"
# Telegram chat ID to send briefings to (numeric string, e.g. "123456789").
TELEGRAM_CHAT_ID = "telegram_chat_id"
# Local time to send the daily briefing, as "HH:MM" (24-hour). Default "07:30".
BRIEFING_SCHEDULE_TIME = "briefing_schedule_time"
# User's UTC offset in minutes using JS convention (UTC+10 → -600, UTC-5 → +300).
BRIEFING_TZ_OFFSET = "briefing_tz_offset"
# ISO date string (YYYY-MM-DD) of the last day a briefing was successfully sent.
BRIEFING_LAST_SENT = "briefing_last_sent"
# Last known device location for weather in scheduled/Telegram briefings.
LAST_KNOWN_LAT = "last_known_lat"
LAST_KNOWN_LON = "last_known_lon"

# Random hex token sent as X-Telegram-Bot-Api-Secret-Token on every webhook POST.
# Generated on first webhook registration; used to verify requests come from Telegram.
TELEGRAM_WEBHOOK_SECRET = "telegram_webhook_secret"
# Local "HH:MM" time to send an evening habit reminder; empty string to disable.
HABIT_REMINDER_TIME = "habit_reminder_time"
# ISO date of last habit reminder sent — prevents double-send.
HABIT_REMINDER_LAST_SENT = "habit_reminder_last_sent"
# Local "HH:MM" time to send a midday overdue-task nudge; empty string to disable.
OVERDUE_NUDGE_TIME = "overdue_nudge_time"
# ISO date of last overdue nudge sent — prevents double-send.
OVERDUE_NUDGE_LAST_SENT = "overdue_nudge_last_sent"

# ISO date of last evening summary sent — prevents double-send.
EVENING_SUMMARY_LAST_SENT = "evening_summary_last_sent"
# JSON {"date": "YYYY-MM-DD", "ids": [...]} — event IDs already alerted today.
MEETING_ALERTS_SENT = "meeting_alerts_sent"

# ── One-time migration flags ───────────────────────────────────────────────────
# Set to "1" once the habit streak_days backfill has completed.
STREAK_DAYS_V1 = "streak_days_v1"
