"""
Unit tests for daily-plan time handling.

Covers:
  - _fmt_time_24h: UTC→local conversion used when building the LLM context
  - _normalize_plan_time: normalises the LLM's time output to canonical HH:MM
  - _build_daily_plan_context: verifies timed tasks appear as FIXED, not "preferred"
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest

from briefing.generate import _fmt_time_24h, _normalize_plan_time, _build_daily_plan_context


# ── _fmt_time_24h ─────────────────────────────────────────────────────────────
# JS getTimezoneOffset() convention: positive = behind UTC (e.g. PDT = 420).

class TestFmtTime24h:
    def test_pdt_afternoon(self):
        """2 PM PDT = 21:00 UTC.  offset=420 (UTC-7)."""
        dt = datetime(2026, 6, 17, 21, 0, 0, tzinfo=timezone.utc)
        assert _fmt_time_24h(dt, 420) == "14:00"

    def test_pdt_morning(self):
        """9 AM PDT = 16:00 UTC."""
        dt = datetime(2026, 6, 17, 16, 0, 0, tzinfo=timezone.utc)
        assert _fmt_time_24h(dt, 420) == "09:00"

    def test_est_morning(self):
        """9 AM EST = 14:00 UTC.  offset=300 (UTC-5)."""
        dt = datetime(2026, 6, 17, 14, 0, 0, tzinfo=timezone.utc)
        assert _fmt_time_24h(dt, 300) == "09:00"

    def test_utc_no_offset(self):
        """With offset=0, UTC time passes through unchanged."""
        dt = datetime(2026, 6, 17, 9, 30, 0, tzinfo=timezone.utc)
        assert _fmt_time_24h(dt, 0) == "09:30"

    def test_east_of_utc(self):
        """9 PM JST = 12:00 UTC.  offset=-540 (UTC+9)."""
        dt = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
        assert _fmt_time_24h(dt, -540) == "21:00"

    def test_naive_datetime_no_offset_applied(self):
        """Naive datetimes (e.g. todo.scheduled_at stored as local) pass through."""
        dt = datetime(2026, 6, 17, 9, 0, 0)  # no tzinfo
        assert _fmt_time_24h(dt, 420) == "09:00"

    def test_with_minutes(self):
        """Minutes are preserved correctly."""
        dt = datetime(2026, 6, 17, 20, 45, 0, tzinfo=timezone.utc)
        assert _fmt_time_24h(dt, 420) == "13:45"  # 8:45 PM UTC = 1:45 PM PDT


# ── _normalize_plan_time ──────────────────────────────────────────────────────

class TestNormalizePlanTime:
    # ── Canonical 24h formats (already correct) ──
    def test_canonical_hhmm(self):
        assert _normalize_plan_time("09:00") == "09:00"

    def test_canonical_hhmm_no_leading_zero(self):
        assert _normalize_plan_time("9:00") == "09:00"

    def test_canonical_hhmm_with_minutes(self):
        assert _normalize_plan_time("14:30") == "14:30"

    def test_canonical_midnight(self):
        assert _normalize_plan_time("00:00") == "00:00"

    def test_canonical_noon(self):
        assert _normalize_plan_time("12:00") == "12:00"

    # ── 24h with seconds (HH:MM:SS) ──
    def test_24h_with_seconds(self):
        assert _normalize_plan_time("14:00:00") == "14:00"

    def test_24h_with_seconds_minutes(self):
        assert _normalize_plan_time("09:30:00") == "09:30"

    # ── 12h format: "H:MM AM/PM" ──
    def test_12h_am(self):
        assert _normalize_plan_time("9:00 AM") == "09:00"

    def test_12h_pm(self):
        assert _normalize_plan_time("2:30 PM") == "14:30"

    def test_12h_noon(self):
        assert _normalize_plan_time("12:00 PM") == "12:00"

    def test_12h_midnight(self):
        assert _normalize_plan_time("12:00 AM") == "00:00"

    def test_12h_pm_no_leading_zero(self):
        assert _normalize_plan_time("3:45 PM") == "15:45"

    def test_12h_lowercase(self):
        assert _normalize_plan_time("9:00 am") == "09:00"

    def test_12h_mixed_case(self):
        assert _normalize_plan_time("2:30 Pm") == "14:30"

    # ── 12h format: "H AM/PM" (no minutes) ──
    def test_12h_no_minutes_am(self):
        assert _normalize_plan_time("9 AM") == "09:00"

    def test_12h_no_minutes_pm(self):
        assert _normalize_plan_time("2 PM") == "14:00"

    def test_12h_no_minutes_noon(self):
        assert _normalize_plan_time("12 PM") == "12:00"

    def test_12h_no_minutes_midnight(self):
        assert _normalize_plan_time("12 AM") == "00:00"

    # ── None / null / unparseable ──
    def test_none_returns_none(self):
        assert _normalize_plan_time(None) is None

    def test_empty_string_returns_none(self):
        assert _normalize_plan_time("") is None

    def test_unparseable_returns_none(self):
        assert _normalize_plan_time("afternoon") is None


# ── _build_daily_plan_context ─────────────────────────────────────────────────

def _make_todo(title, scheduled_at=None):
    t = MagicMock()
    t.title = title
    t.scheduled_at = scheduled_at
    return t


def _make_event(title, start, end=None, all_day=False):
    e = MagicMock()
    e.title = title
    e.start = start
    e.end = end
    e.all_day = all_day
    return e


class TestBuildDailyPlanContext:
    TODAY = datetime(2026, 6, 17).date()
    PDT = 420  # UTC-7

    def test_timed_task_appears_in_fixed_section(self):
        """A task with scheduled_at must land in the 'fixed start time' section."""
        # 7 PM PDT = 02:00 UTC next day — stored as naive local: 19:00
        todo = _make_todo("Evening review", scheduled_at=datetime(2026, 6, 17, 19, 0, 0))
        ctx = _build_daily_plan_context(self.TODAY, [], [todo], [], self.PDT)
        assert "fixed start time" in ctx
        assert "19:00" in ctx
        assert "Evening review" in ctx
        # Must NOT appear in the flexible/unscheduled section
        assert "Unscheduled tasks" not in ctx

    def test_timed_task_uses_starts_at_language(self):
        """Context must say 'starts at HH:MM', not 'preferred time'."""
        todo = _make_todo("Call dentist", scheduled_at=datetime(2026, 6, 17, 14, 0, 0))
        ctx = _build_daily_plan_context(self.TODAY, [], [todo], [], 0)
        assert "starts at 14:00" in ctx
        assert "preferred" not in ctx

    def test_untimed_task_appears_in_unscheduled_section(self):
        """A task without scheduled_at must land in the flexible section only."""
        todo = _make_todo("Write report")
        ctx = _build_daily_plan_context(self.TODAY, [], [todo], [], self.PDT)
        assert "Unscheduled tasks" in ctx
        assert "Write report" in ctx
        assert "fixed start time" not in ctx

    def test_timed_and_untimed_tasks_separated(self):
        """Timed and untimed tasks must appear in separate sections."""
        timed   = _make_todo("Meeting", scheduled_at=datetime(2026, 6, 17, 10, 0, 0))
        untimed = _make_todo("Review docs")
        ctx = _build_daily_plan_context(self.TODAY, [], [timed, untimed], [], 0)
        assert "fixed start time" in ctx
        assert "Meeting" in ctx
        assert "Unscheduled tasks" in ctx
        assert "Review docs" in ctx

    def test_calendar_event_with_pdt_offset(self):
        """UTC-aware calendar event time is converted to local time in context."""
        # 2 PM PDT = 21:00 UTC
        event = _make_event("Standup", start=datetime(2026, 6, 17, 21, 0, 0, tzinfo=timezone.utc))
        ctx = _build_daily_plan_context(self.TODAY, [event], [], [], self.PDT)
        assert "14:00" in ctx   # 21:00 UTC - 7h = 14:00 PDT
        assert "Standup" in ctx

    def test_calendar_event_with_end_time(self):
        """End time is included in the context range."""
        event = _make_event(
            "Workshop",
            start=datetime(2026, 6, 17, 16, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 17, 18, 0, 0, tzinfo=timezone.utc),
        )
        ctx = _build_daily_plan_context(self.TODAY, [event], [], [], self.PDT)
        assert "09:00" in ctx   # 16:00 UTC - 7h
        assert "11:00" in ctx   # 18:00 UTC - 7h
