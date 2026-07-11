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

# ── One-time migration flags ───────────────────────────────────────────────────
# Set to "1" once the habit streak_days backfill has completed.
STREAK_DAYS_V1 = "streak_days_v1"
