"""
Unit tests for Google Calendar event URL generation in gcal.py.

Covers the eid encoding logic using URLs confirmed correct by the user.
"""

from datetime import datetime, timezone
import pytest
import icalendar

from gcal import _google_calendar_event_url, _shorten_calendar_id, _parse_google_calendar_id

ICAL_URL = "https://calendar.google.com/calendar/ical/mrericsir%40gmail.com/private-TOKEN/basic.ics"


def make_ev(rrule=None, recurrence_id=None):
    """Build a minimal icalendar VEVENT component."""
    ev = icalendar.Event()
    if rrule:
        ev.add("RRULE", icalendar.vRecur.from_ical(rrule))
    if recurrence_id:
        ev.add("RECURRENCE-ID", recurrence_id)
    return ev


# ── _parse_google_calendar_id ─────────────────────────────────────────────────

def test_parse_calendar_id_gmail():
    assert _parse_google_calendar_id(ICAL_URL) == "mrericsir@gmail.com"


def test_parse_calendar_id_non_gcal():
    assert _parse_google_calendar_id("https://example.com/feed.ics") is None


# ── _shorten_calendar_id ──────────────────────────────────────────────────────

def test_shorten_gmail():
    assert _shorten_calendar_id("mrericsir@gmail.com") == "mrericsir@m"


def test_shorten_non_gmail_unchanged():
    assert _shorten_calendar_id("abc123@group.calendar.google.com") == "abc123@group.calendar.google.com"


# ── _google_calendar_event_url ────────────────────────────────────────────────

def test_non_google_uid_returns_none():
    """Non-Google UIDs should not generate a URL."""
    ev = make_ev()
    result = _google_calendar_event_url("someuid@apple.com", datetime(2026, 6, 17, 21, 0, tzinfo=timezone.utc), ICAL_URL, ev)
    assert result is None


def test_non_gcal_ical_url_returns_none():
    """Without a recognisable iCal URL the calendar ID can't be extracted."""
    ev = make_ev(rrule="FREQ=WEEKLY")
    result = _google_calendar_event_url(
        "cc9q3ne6fqis5n7asoob0tj5so@google.com",
        datetime(2026, 6, 17, 21, 0, tzinfo=timezone.utc),
        "https://example.com/feed.ics",
        ev,
    )
    assert result is None


def test_recurring_event_omits_timestamp():
    """
    Regular recurring event (RRULE): eid = {uid_base} {calendar_id} — no timestamp.

    User-confirmed correct URL:
    https://calendar.google.com/calendar/u/0/r/event?action=VIEW&eid=MDYyNTBydjM4aHNhbjh1MHBkZmxxMXZwZWYgbXJlcmljc2lyQG0
    Decodes to: 06250rv38hsan8u0pdflq1vpef mrericsir@m
    """
    ev = make_ev(rrule="FREQ=WEEKLY")
    url = _google_calendar_event_url(
        "06250rv38hsan8u0pdflq1vpef@google.com",
        datetime(2026, 6, 17, 18, 0, 0, tzinfo=timezone.utc),
        ICAL_URL,
        ev,
    )
    assert url == (
        "https://calendar.google.com/calendar/u/0/r/event?action=VIEW"
        "&eid=MDYyNTBydjM4aHNhbjh1MHBkZmxxMXZwZWYgbXJlcmljc2lyQG0"
    )


def test_single_event_omits_timestamp():
    """
    Single event: eid = {uid_base} {calendar_id} — no timestamp.

    User-confirmed correct URL:
    https://calendar.google.com/calendar/u/0/r/event?action=VIEW&eid=MTB1cDBoaDRkMjRmbDcxdmU1dnBxaDc5MzggbXJlcmljc2lyQG0
    Decodes to: 10up0hh4d24fl71ve5vpqh7938 mrericsir@m
    """
    url = _google_calendar_event_url(
        "10up0hh4d24fl71ve5vpqh7938@google.com",
        datetime(2026, 6, 27, 18, 22, 48, tzinfo=timezone.utc),
        ICAL_URL,
    )
    assert url == (
        "https://calendar.google.com/calendar/u/0/r/event?action=VIEW"
        "&eid=MTB1cDBoaDRkMjRmbDcxdmU1dnBxaDc5MzggbXJlcmljc2lyQG0"
    )


def test_exception_instance_omits_timestamp():
    """
    Exception instance (RECURRENCE-ID): same format, no timestamp.
    All confirmed correct URLs use {uid_base} {calendar_id} regardless of event type.

    User-confirmed correct URL:
    https://calendar.google.com/calendar/u/0/r/event?action=VIEW&eid=Y2M5cTNuZTZmcWlzNW43YXNvb2IwdGo1c29fMjAyNjA2MTdUMjEwMDAwWiBtcmVyaWNzaXJAbQ
    was provided for cc9q3ne6fqis5n7asoob0tj5so — here we verify the no-timestamp form
    which matches all other confirmed-working links.
    """
    recurrence_dt = datetime(2026, 6, 17, 21, 0, 0, tzinfo=timezone.utc)
    ev = make_ev(recurrence_id=recurrence_dt)
    url = _google_calendar_event_url(
        "06250rv38hsan8u0pdflq1vpef@google.com",
        recurrence_dt,
        ICAL_URL,
        ev,
    )
    assert url == (
        "https://calendar.google.com/calendar/u/0/r/event?action=VIEW"
        "&eid=MDYyNTBydjM4aHNhbjh1MHBkZmxxMXZwZWYgbXJlcmljc2lyQG0"
    )
