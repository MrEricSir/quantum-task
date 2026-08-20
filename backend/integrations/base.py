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

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol


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


@dataclass
class Measurement:
    """A single dated reading for one canonical metric name (e.g. "steps", "weight")."""
    date: str    # ISO YYYY-MM-DD
    value: float


class HealthProvider(Protocol):
    """A source of health measurements gated behind OAuth.

    refresh() and exchange_code() take `db` explicitly, unlike CalendarProvider's stateless
    methods -- this isn't a stylistic choice, it's forced by at least one real provider:
    Withings rotates its refresh token on every use (the old one is invalidated the instant a
    new one is issued), so persisting the refreshed credentials is not a separable, deferrable
    step a caller could choose to do later. See integrations/withings.py and
    routers/withings.py's do_sync() docstring for the production incident (two unsynchronized
    callers racing a refresh) this constraint protects against. `db` is typed loosely (`Any`)
    here rather than importing SQLAlchemy's `Session` into this otherwise dependency-free file.
    """

    def auth_url(self) -> str:
        """Build the provider's OAuth authorization URL. Raises if not configured."""
        ...

    def exchange_code(self, code: str, db: Any) -> dict:
        """Exchange an OAuth authorization code for credentials and persist them.

        Returns a plain credentials dict (provider-specific shape, opaque to callers) rather
        than a wrapper type -- matches how Withings' own code already treats credentials as a
        dict everywhere, so the interface doesn't invent a type nothing else uses yet.
        """
        ...

    def sync(self, creds: dict) -> dict[str, list[Measurement]]:
        """Fetch recent readings. Pure read -- no DB access, no persistence.

        Returns canonical metric name -> readings, e.g. {"steps": [Measurement(...), ...]}.
        """
        ...

    def refresh(self, creds: dict, db: Any) -> dict:
        """Exchange a refresh token for a new access token and persist it. See class docstring
        for why persistence isn't optional here."""
        ...
