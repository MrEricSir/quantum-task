"""
Unit tests for recurring calendar event condensing in _build_week_context.
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from briefing.context import build_week_context as _build_week_context


TODAY = date(2026, 6, 3)


def make_event(title, day_offset, hour=10, minute=0, all_day=False):
    d = TODAY + timedelta(days=day_offset)
    start = datetime(d.year, d.month, d.day) if all_day else datetime(d.year, d.month, d.day, hour, minute)
    return SimpleNamespace(title=title, start=start, all_day=all_day)


def make_todo(title, day_offset=None):
    if day_offset is not None:
        d = TODAY + timedelta(days=day_offset)
        scheduled_at = datetime(d.year, d.month, d.day, 9, 0)
    else:
        scheduled_at = None
    return SimpleNamespace(title=title, scheduled_at=scheduled_at)


class TestRecurringCalendarEvents:
    def test_single_occurrence_not_grouped(self):
        """An event appearing only once is rendered per-day, not in a recurring block."""
        result = _build_week_context([], [make_event('Team Standup', 1)], TODAY)
        assert 'Recurring this week' not in result
        assert 'Team Standup' in result

    def test_two_occurrences_are_grouped(self):
        """Same title and time on two days produces a single recurring entry."""
        events = [make_event('Team Standup', 1), make_event('Team Standup', 3)]
        result = _build_week_context([], events, TODAY)
        assert 'Recurring this week' in result
        assert result.count('Team Standup') == 1

    def test_three_occurrences_show_day_abbreviations(self):
        """Three same-title events list all day abbreviations on a single line."""
        events = [
            make_event('Team Standup', 1),
            make_event('Team Standup', 3),
            make_event('Team Standup', 5),
        ]
        result = _build_week_context([], events, TODAY)
        assert 'Recurring this week' in result
        assert result.count('Team Standup') == 1
        standup_line = next(l for l in result.splitlines() if 'Team Standup' in l)
        # Day abbreviations separated from title by an em-dash
        assert '\u2014' in standup_line

    def test_all_day_recurring_events_are_grouped(self):
        """All-day events with the same title are condensed."""
        events = [
            make_event('Sprint Review', 1, all_day=True),
            make_event('Sprint Review', 4, all_day=True),
        ]
        result = _build_week_context([], events, TODAY)
        assert 'Recurring this week' in result
        assert result.count('Sprint Review') == 1
        assert 'all day' in result

    def test_different_times_not_grouped(self):
        """Same title but different times are NOT grouped — they are distinct events."""
        events = [make_event('Standup', 1, hour=9), make_event('Standup', 3, hour=14)]
        result = _build_week_context([], events, TODAY)
        assert 'Recurring this week' not in result
        assert result.count('Standup') == 2

    def test_mixed_recurring_and_single_events(self):
        """Recurring entries appear in their own block; single events still show per-day."""
        events = [
            make_event('Team Standup', 1),
            make_event('Team Standup', 3),
            make_event('One-off Meeting', 2),
        ]
        result = _build_week_context([], events, TODAY)
        assert 'Recurring this week' in result
        assert result.count('Team Standup') == 1
        assert 'One-off Meeting' in result

    def test_today_events_excluded_from_recurrence_count(self):
        """An event on today's date is excluded; only future occurrences count."""
        # day_offset=0 is today, day_offset=1 is tomorrow → only 1 future occurrence
        events = [make_event('Daily Sync', 0), make_event('Daily Sync', 1)]
        result = _build_week_context([], events, TODAY)
        assert 'Recurring this week' not in result

    def test_returns_none_with_no_content(self):
        assert _build_week_context([], [], TODAY) is None

    def test_todos_are_unaffected_by_recurring_logic(self):
        """Todo items still appear under their day heading regardless of recurrence logic."""
        todos = [make_todo('Write report', day_offset=2)]
        result = _build_week_context(todos, [], TODAY)
        assert 'Write report' in result
        assert 'Recurring this week' not in result

    def test_multiple_recurring_series(self):
        """Two independent recurring event series are both condensed."""
        events = [
            make_event('Standup', 1),
            make_event('Standup', 3),
            make_event('1:1', 2),
            make_event('1:1', 4),
        ]
        result = _build_week_context([], events, TODAY)
        assert 'Recurring this week' in result
        assert result.count('Standup') == 1
        assert result.count('1:1') == 1
