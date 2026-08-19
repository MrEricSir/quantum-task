"""
ICS feed implementation of CalendarProvider.

Provider-neutral by construction: any ICS feed URL works (Google Calendar's "Secret address
in iCal format", Outlook, Apple Calendar, Fastmail, ...). The only provider-specific piece is
event_deep_link, which builds a Google Calendar UI link when the event's UID marks it as a
Google-hosted event, and returns None otherwise — a second ICS provider that wants its own
deep links would extend that method, not fork the whole class.

This wraps gcal.py's existing module-level functions rather than reimplementing them: gcal.py
is still imported directly by ~8 call sites (routers/calendar.py, routers/discovery.py,
assist/context.py, routers/assist.py, briefing/generate.py, telegram/bot.py,
telegram/scheduler.py) and none of them go through this class yet. See
ARCHITECTURE_FUTURE.md Step 2 — the interface is formalized here without rewiring existing
callers, since nothing today needs a second calendar provider.
"""

from datetime import date, datetime

import gcal


class IcsCalendarProvider:
    """Implements CalendarProvider by delegating to gcal.py."""

    def normalize_url(self, url: str) -> str:
        return gcal.normalize_ical_url(url)

    def fetch_events(self, feed_url: str, start: date, end: date) -> list[dict]:
        return gcal.fetch_events(feed_url, start, end)

    def event_deep_link(self, uid: str, start_dt: datetime, feed_url: str, ev=None) -> str | None:
        return gcal._google_calendar_event_url(uid, start_dt, feed_url, ev)
