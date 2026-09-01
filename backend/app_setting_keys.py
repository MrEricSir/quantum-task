"""
Constants for all keys stored in the ``app_settings`` table.

Every key used anywhere in the codebase should be listed here with a comment
explaining its value format and purpose.
"""

# ── Auth ────────────────────────────────────────────────────────────────────
# Random secret backing the "session" cookie value. Generated on first login;
# rotating it (see routers/auth.py's logout) invalidates every outstanding
# session cookie at once. Deliberately independent of AUTH_PASSWORD -- see
# deps.py's Auth config comment for why.
SESSION_SECRET = "session_secret"
# Integer string: consecutive failed /api/auth/login attempts since the last
# success or lockout. Reset to "0" on a successful login.
AUTH_FAILED_ATTEMPTS = "auth_failed_attempts"
# ISO 8601 UTC timestamp: login is locked out until this time (set once
# AUTH_FAILED_ATTEMPTS crosses the threshold in routers/auth.py). Empty/absent
# means not currently locked out.
AUTH_LOCKOUT_UNTIL = "auth_lockout_until"

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
# JSON dict mapping repo ("owner/repo" or "default") to {"in_progress": str, "done": str}
# column names on the GitHub Projects v2 board.
GITHUB_STATUS_CONFIG = "github_status_config"
# JSON dict mapping a repo pattern ("owner/repo" or just "owner", matching all
# of that owner's repos) to a list of tag IDs applied to cards created from it.
GITHUB_REPO_TAGS = "github_repo_tags"

# ── Withings health goals (still in AppSetting — not yet migrated) ─────────────
# JSON object {"steps": N|null, "fat_ratio": N|null, "weight": N|null}.
WITHINGS_HEALTH_GOALS = "withings_health_goals"

# "1" once a Telegram notification has been sent for the current run of
# invalid_token failures; cleared on the next successful refresh, so a
# fresh failure after reconnecting notifies again instead of staying silent.
WITHINGS_AUTH_FAILURE_NOTIFIED = "withings_auth_failure_notified"

# Pending OAuth "state" value for the in-flight Withings authorization attempt.
# Set when /api/withings/auth-url is called, checked and cleared on
# /api/withings/callback -- guards against CSRF (an attacker tricking the user's
# browser into completing an authorization the user never started). Single value
# because this is a single-user app with one in-flight OAuth attempt at a time.
WITHINGS_OAUTH_STATE = "withings_oauth_state"

# ── Telegram ──────────────────────────────────────────────────────────
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
# Last known device location for weather in scheduled/Telegram.
LAST_KNOWN_LAT = "last_known_lat"
LAST_KNOWN_LON = "last_known_lon"

# Event Discovery: events farther than this from the last known location are filtered out.
# Stored canonically in miles regardless of DISCOVERY_DISTANCE_UNIT (display-only).
DISCOVERY_MAX_DISTANCE_MILES = "discovery_max_distance_miles"
# Which unit the Calendar Settings UI shows/accepts the distance in: "mi" or "km".
DISCOVERY_DISTANCE_UNIT = "discovery_distance_unit"

# Random hex token sent as X-Telegram-Bot-Api-Secret-Token on every webhook POST.
# Generated on first webhook registration; used to verify requests come from Telegram.
TELEGRAM_WEBHOOK_SECRET = "telegram_webhook_secret"
# Integer string: the highest Telegram update_id already processed. Telegram resends
# an update if our webhook response doesn't arrive in time (e.g. a slow LLM call
# during a Cloud Run cold start); this drops those retries instead of reprocessing
# and re-sending the same reply.
TELEGRAM_LAST_UPDATE_ID = "telegram_last_update_id"
# Local "HH:MM" time to send an evening habit reminder; empty string to disable.
HABIT_REMINDER_TIME = "habit_reminder_time"
# Local "HH:MM" time to send a midday overdue-task nudge; empty string to disable.
OVERDUE_NUDGE_TIME = "overdue_nudge_time"
# ISO date of last overdue nudge sent — prevents double-send.
OVERDUE_NUDGE_LAST_SENT = "overdue_nudge_last_sent"

# ISO date of last evening summary sent — prevents double-send.
EVENING_SUMMARY_LAST_SENT = "evening_summary_last_sent"

# Day + local "HH:MM" time to send the weekly review, as "DOW:HH:MM" (DOW is a
# 3-letter uppercase weekday, e.g. "SUN:18:00"). Default "SUN:18:00".
WEEKLY_REVIEW_SCHEDULE_TIME = "weekly_review_schedule_time"
# ISO week string (e.g. "2026-W33") of the last week a review was sent — prevents double-send.
WEEKLY_REVIEW_LAST_SENT = "weekly_review_last_sent"
# JSON {"date": "YYYY-MM-DD", "ids": [...]} — event IDs already alerted today.
MEETING_ALERTS_SENT = "meeting_alerts_sent"
# JSON {"habit_id:milestone": "YYYY-MM-DD"} — tracks when each streak milestone was sent.
STREAK_MILESTONES_SENT = "streak_milestones_sent"
# JSON {signal_key: "YYYY-MM-DD"} — last-notified date per health nudge signal
# (e.g. "streak_risk:3", "going_cold:5", "food_log_quiet", "withings_drift:7").
# Each signal has its own cooldown so a persistent issue isn't re-flagged every day.
HEALTH_NUDGES_SENT = "health_nudges_sent"

# ── Bridge job notifications ───────────────────────────────────────────────────
# Integer string: the highest BridgeJob.id whose completion has been Telegram-notified.
BRIDGE_LAST_NOTIFIED_JOB = "bridge_last_notified_job"
# Integer string: the highest BridgeJob.id whose start (running) has been Telegram-notified.
BRIDGE_LAST_NOTIFIED_RUNNING_JOB = "bridge_last_notified_running_job"
# Scoped, rotatable secret required as ?token= on GET /api/bridge/install.py — lets that
# endpoint be curl-able with no session while keeping it separate from AUTH_PASSWORD.
BRIDGE_INSTALL_TOKEN = "bridge_install_token"
# Scoped, rotatable secret baked into the served install script (config.json on the
# user's machine) so the installed qtask-bridge CLI can authenticate its own ongoing
# requests. Deliberately separate from AUTH_PASSWORD: it lives in a local file on
# whatever machine ran the installer, and can be rotated (revoking every installed
# CLI's credential) without changing the real app login password. Accepted as an
# alternative Bearer credential in main.py's AuthMiddleware.
BRIDGE_TOKEN = "bridge_token"

# ── One-time migration flags ───────────────────────────────────────────────────
# Set to "1" once the habit streak_days backfill has completed.
STREAK_DAYS_V1 = "streak_days_v1"

# ── Navigation preferences ──────────────────────────────────────────────────────
# JSON array of page ids controlling sidebar/mobile-nav order, e.g.
# ["today","board","calendar","health","engineering"]. May be a subset or miss newly
# added pages -- routers/preferences.py fills in the gaps against its known page list.
NAV_ORDER = "nav_order"
# Page id (one of routers/preferences.py's NAV_PAGE_IDS) to redirect to on "/".
DEFAULT_PAGE = "default_page"
