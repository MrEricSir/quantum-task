"""
Tests for integrations/calendar_ics.py: IcsCalendarProvider delegates to gcal.py.

These are deliberately thin — the actual fetch/parse/URL logic is already covered by
tests/test_calendar.py and tests/test_gcal_urls.py against gcal.py directly. This file only
verifies the CalendarProvider-shaped wrapper calls through with the right arguments.
"""

from datetime import date, datetime, timezone
from unittest.mock import patch

from integrations.calendar_ics import IcsCalendarProvider


def test_normalize_url_delegates_to_gcal():
    provider = IcsCalendarProvider()
    with patch("gcal.normalize_ical_url", return_value="https://normalized") as mock_normalize:
        result = provider.normalize_url("webcal://example.com/feed.ics")
    mock_normalize.assert_called_once_with("webcal://example.com/feed.ics")
    assert result == "https://normalized"


def test_fetch_events_delegates_to_gcal():
    provider = IcsCalendarProvider()
    start = date(2026, 8, 19)
    end = date(2026, 9, 16)
    fake_events = [{"id": "abc", "title": "Standup"}]
    with patch("gcal.fetch_events", return_value=fake_events) as mock_fetch:
        result = provider.fetch_events("https://example.com/feed.ics", start, end)
    mock_fetch.assert_called_once_with("https://example.com/feed.ics", start, end)
    assert result == fake_events


def test_event_deep_link_delegates_to_gcal():
    provider = IcsCalendarProvider()
    start_dt = datetime(2026, 8, 19, 15, 0, tzinfo=timezone.utc)
    with patch("gcal._google_calendar_event_url", return_value="https://calendar.google.com/x") as mock_link:
        result = provider.event_deep_link("uid@google.com", start_dt, "https://feed", ev="ev-obj")
    mock_link.assert_called_once_with("uid@google.com", start_dt, "https://feed", "ev-obj")
    assert result == "https://calendar.google.com/x"


def test_event_deep_link_none_for_non_google_events():
    provider = IcsCalendarProvider()
    start_dt = datetime(2026, 8, 19, 15, 0, tzinfo=timezone.utc)
    result = provider.event_deep_link("uid@outlook.com", start_dt, "https://feed")
    assert result is None
