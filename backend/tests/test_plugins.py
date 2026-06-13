"""
Unit tests for model plugin post-processing logic in model_plugins/base.py.

These are pure unit tests — no LLM, no server, no database.
They verify that the deterministic overrides in BaseModelPlugin.post_process()
enforce the rules stated in the prompt even when the LLM returns wrong values.
"""
import re
import pytest
from datetime import date, datetime
from model_plugins.base import BaseModelPlugin, resolve_dates
from model_plugins.llama31_8b import Llama31_8bPlugin
from schemas import ParsedTodo

plugin = BaseModelPlugin()


def _parsed(**kwargs) -> ParsedTodo:
    """Build a minimal ParsedTodo with sensible defaults."""
    defaults = dict(type="task", title="Test task", section="later")
    defaults.update(kwargs)
    return ParsedTodo(**defaults)


def _pp(text: str, **kwargs) -> ParsedTodo:
    """Run post_process on a minimal ParsedTodo with the given input text."""
    return plugin.post_process(_parsed(**kwargs), text=text)


# ── Section overrides from input text ────────────────────────────────────────

class TestSectionOverrides:
    """
    Deterministic section assignment based on temporal phrases in the input.
    The LLM often returns a valid but wrong section for these well-known phrases;
    post_process must correct it regardless of what the LLM returned.
    """

    @pytest.mark.parametrize("text", [
        "dentist appointment next week",
        "meeting NEXT WEEK",
        "submit report next week please",
    ])
    def test_next_week_forces_week(self, text):
        result = _pp(text, section="month")   # LLM returned wrong value
        assert result.section == "week", f"'next week' in {text!r} must yield section='week'"

    @pytest.mark.parametrize("text", [
        "team sync this week",
        "finish draft this week",
    ])
    def test_this_week_forces_week(self, text):
        result = _pp(text, section="month")
        assert result.section == "week"

    @pytest.mark.parametrize("text", [
        "call stacy tomorrow",
        "dentist tomorrow at 3pm",
    ])
    def test_tomorrow_forces_week(self, text):
        result = _pp(text, section="today")
        assert result.section == "week"

    @pytest.mark.parametrize("text", [
        "meeting this Monday",
        "lunch this Friday",
        "review this Thursday",
    ])
    def test_this_weekday_forces_week(self, text):
        result = _pp(text, section="month")
        assert result.section == "week"

    @pytest.mark.parametrize("text", [
        "performance review next month",
        "trip planning next month",
    ])
    def test_next_month_forces_month(self, text):
        result = _pp(text, section="later")
        assert result.section == "month"

    @pytest.mark.parametrize("text", [
        "follow up in two weeks",
        "check back in two weeks",
    ])
    def test_in_two_weeks_forces_month(self, text):
        result = _pp(text, section="week")
        assert result.section == "month"

    @pytest.mark.parametrize("text", [
        "project deadline in three weeks",
    ])
    def test_in_three_weeks_forces_month(self, text):
        result = _pp(text, section="week")
        assert result.section == "month"

    def test_no_temporal_phrase_leaves_section_unchanged(self):
        result = _pp("buy groceries", section="later")
        assert result.section == "later"

    def test_no_temporal_phrase_leaves_week_unchanged(self):
        # If the LLM correctly assigned "week" for some other reason, don't touch it.
        result = _pp("call john", section="week")
        assert result.section == "week"


# ── Reference-section overrides from capture prefixes ─────────────────────────

class TestNoteTypeOverrides:
    """
    Inputs that start with capture phrases must always produce section='none'
    (reference card) regardless of what the LLM returned.
    The 'note' type was merged into tasks; capture-prefix inputs now become
    type='task' with section='none'.
    """

    @pytest.mark.parametrize("prefix,text", [
        ("idea:",     "idea: build a habit tracker with streaks"),
        ("note:",     "note: remember to water the plants"),
        ("thought:",  "thought: maybe switch to Postgres"),
        ("remember:", "remember: Sarah's birthday is June 12"),
        ("jot down",  "jot down my wifi password"),
        ("write down","write down the recipe"),
    ])
    def test_prefix_forces_reference_section(self, prefix, text):
        result = _pp(text, type="task")
        assert result.section == "none", \
            f"Prefix {prefix!r} must force section='none', got {result.section!r}"
        assert result.type == "task"

    def test_note_prefix_populates_description_when_missing(self):
        result = _pp("idea: build a habit tracker", type="task")
        assert result.description is not None
        assert "habit tracker" in result.description

    def test_note_prefix_preserves_existing_description(self):
        result = _pp("idea: build a habit tracker", type="task", description="already set")
        assert result.description == "already set"

    def test_non_prefix_task_type_unchanged(self):
        result = _pp("buy groceries", type="task")
        assert result.type == "task"

    def test_list_input_becomes_reference_section(self):
        # List-like inputs are routed to section='none' regardless of LLM output.
        result = _pp("packing list for the trip", type="task", description="- [ ] passport")
        assert result.section == "none"
        assert result.type == "task"


# ── Recurrence section fix (pre-existing behaviour) ──────────────────────────

class TestRecurrenceSection:
    def test_daily_recurrence_overrides_later(self):
        result = _pp("meditate daily", recurrence_rule="daily", section="later")
        assert result.section == "today"

    def test_weekly_recurrence_overrides_later(self):
        result = _pp("review metrics every week", recurrence_rule="weekly", section="later")
        assert result.section == "week"

    def test_recurrence_does_not_override_explicit_section(self):
        result = _pp("meditate daily", recurrence_rule="daily", section="today")
        assert result.section == "today"


# ── Bulk timestamp parsing ─────────────────────────────────────────────────────
# Regression: "call tom at 6pm, dinner with andre at 7"
# The LLM (llama-3.1-8b-instant) sometimes embeds ISO datetimes in the title
# field instead of (or in addition to) scheduled_at, and may not resolve bare
# "at N" hour references without am/pm.

_llama = Llama31_8bPlugin()
_TODAY = date(2026, 6, 7)


def _full_pipeline(raw: dict, *, text: str = "") -> ParsedTodo:
    """normalize_raw → post_process → resolve_dates, using the Llama 3.1 8b plugin."""
    raw = _llama.normalize_raw(raw)
    parsed = _llama.post_process(ParsedTodo.model_validate(raw), text=text)
    return resolve_dates(parsed, text=text, today=_TODAY)


def _task(**overrides) -> dict:
    base = dict(type="task", title="Test", description=None, section="later",
                scheduled_at=None, suggested_tags=[], recurrence_rule=None, note_content=None)
    base.update(overrides)
    return base


class TestBulkTimestampParsing:
    """
    Regression: "call tom at 6pm, dinner with andre at 7" produced two events
    where the ISO datetime appeared in the title and the actual task name was
    hidden in the description (or the scheduled_at was missing entirely).
    """

    def test_iso_embedded_in_title_is_extracted(self):
        """LLM appends the resolved ISO datetime inside the title string."""
        result = _full_pipeline(
            _task(title="Call Tom 2026-06-07T18:00:00", section="today"),
            text="call tom at 6pm",
        )
        assert not re.search(r'\d{4}-\d{2}-\d{2}', result.title), \
            f"ISO date fragment leaked into title: {result.title!r}"
        assert "tom" in result.title.lower()
        assert result.scheduled_at == datetime(2026, 6, 7, 18, 0, 0)

    def test_iso_as_entire_title_promotes_description(self):
        """LLM uses the ISO datetime as the entire title; real name is in description."""
        result = _full_pipeline(
            _task(title="2026-06-07T18:00:00", description="Call Tom",
                  scheduled_at="2026-06-07T18:00:00", section="today"),
            text="call tom at 6pm",
        )
        assert not re.search(r'\d{4}-\d{2}-\d{2}', result.title), \
            f"ISO date fragment leaked into title: {result.title!r}"
        assert result.title.lower() == "call tom"
        assert result.scheduled_at == datetime(2026, 6, 7, 18, 0, 0)

    def test_bare_at_hour_no_ampm_resolves_to_pm(self):
        """'at 7' without am/pm should resolve to 19:00 on today's date."""
        result = _full_pipeline(
            _task(title="Dinner with Andre", section="later"),
            text="dinner with andre at 7",
        )
        assert result.scheduled_at == datetime(2026, 6, 7, 19, 0, 0), \
            f"'at 7' should resolve to 19:00, got: {result.scheduled_at!r}"
        assert result.section == "today"

    def test_iso_in_title_dinner_at_7(self):
        """LLM puts ISO datetime as title for the 'at 7' item; name is in description."""
        result = _full_pipeline(
            _task(title="2026-06-07T19:00:00", description="Dinner with Andre",
                  scheduled_at="2026-06-07T19:00:00", section="today"),
            text="dinner with andre at 7",
        )
        assert not re.search(r'\d{4}-\d{2}-\d{2}', result.title), \
            f"ISO date fragment leaked into title: {result.title!r}"
        assert result.title.lower() == "dinner with andre"
        assert result.scheduled_at == datetime(2026, 6, 7, 19, 0, 0)

    def test_explicit_6pm_resolves_correctly(self):
        """'at 6pm' explicit → 18:00 today, section='today'."""
        result = _full_pipeline(
            _task(title="Call Tom", section="later"),
            text="call tom at 6pm",
        )
        assert result.scheduled_at == datetime(2026, 6, 7, 18, 0, 0)
        assert result.section == "today"

    @pytest.mark.parametrize("raw_title,description", [
        ("2026-06-07T18:00:00",              "Call Tom"),
        ("Call Tom 2026-06-07T18:00:00",     None),
        ("2026-06-07T19:00:00 Dinner Andre", None),
    ])
    def test_title_never_contains_iso_fragment(self, raw_title, description):
        """No LLM output variant should leave an ISO date string in the title."""
        result = _full_pipeline(_task(title=raw_title, description=description))
        assert not re.search(r'\d{4}-\d{2}-\d{2}', result.title), \
            f"ISO date fragment in title for input {raw_title!r}: got {result.title!r}"
