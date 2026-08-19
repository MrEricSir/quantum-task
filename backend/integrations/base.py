"""
Provider interfaces for pluggable external integrations.

See ARCHITECTURE_FUTURE.md ("Part 2: Pluggable External Integrations", Step 2) for the
rationale. Each Protocol names the shape a provider *would* implement; today, each category
still has exactly one concrete implementation, and callers keep importing that implementation
directly rather than going through these Protocols. The Protocols exist so a second provider
(GitLab, Outlook, Oura, ...) has a shape to fit into instead of inventing its own, and so the
one existing implementation can be checked against that shape now, while it's cheap.

Nothing dispatches through these Protocols yet. That's Step 3 (provider registry + generic
credential storage), not built until a real second provider exists.
"""

from datetime import date, datetime
from typing import Protocol


class CalendarProvider(Protocol):
    """A source of calendar events reachable from a single feed URL."""

    def normalize_url(self, url: str) -> str:
        """Normalize a user-pasted URL into a fetchable feed URL.

        e.g. webcal:// -> https://, Google Calendar embed viewer links -> iCal feed links.
        """
        ...

    def fetch_events(self, feed_url: str, start: date, end: date) -> list[dict]:
        """Fetch and expand events from the feed in [start, end).

        Each dict: id, uid, sequence, title, description, location, url, start (datetime),
        end (datetime|None), all_day (bool), is_ooo (bool).
        """
        ...

    def event_deep_link(self, uid: str, start_dt: datetime, feed_url: str, ev=None) -> str | None:
        """Build a URL to the event in the provider's own UI, or None if not derivable."""
        ...
