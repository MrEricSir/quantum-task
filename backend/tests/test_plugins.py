"""
Unit tests for model plugin post-processing logic in model_plugins/base.py.

These are pure unit tests — no LLM, no server, no database.
They verify that the deterministic overrides in BaseModelPlugin.post_process()
enforce the rules stated in the prompt even when the LLM returns wrong values.
"""
import pytest
from model_plugins.base import BaseModelPlugin
from schemas import ParsedTodo

plugin = BaseModelPlugin()


def _parsed(**kwargs) -> ParsedTodo:
    """Build a minimal ParsedTodo with sensible defaults."""
    defaults = dict(type="task", title="Test task", section="later", note_content=None)
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


# ── Note-type overrides from capture prefixes ─────────────────────────────────

class TestNoteTypeOverrides:
    """
    Inputs that start with capture phrases must always produce type='note'
    regardless of what the LLM returned.
    """

    @pytest.mark.parametrize("prefix,text", [
        ("idea:",     "idea: build a habit tracker with streaks"),
        ("note:",     "note: remember to water the plants"),
        ("thought:",  "thought: maybe switch to Postgres"),
        ("remember:", "remember: Sarah's birthday is June 12"),
        ("jot down",  "jot down my wifi password"),
        ("write down","write down the recipe"),
    ])
    def test_prefix_forces_note_type(self, prefix, text):
        result = _pp(text, type="task")   # LLM returned wrong type
        assert result.type == "note", \
            f"Prefix {prefix!r} must force type='note', got {result.type!r}"

    def test_note_prefix_populates_note_content_when_missing(self):
        result = _pp("idea: build a habit tracker", type="task", note_content=None)
        assert result.note_content is not None
        assert "habit tracker" in result.note_content

    def test_note_prefix_preserves_existing_note_content(self):
        result = _pp("idea: build a habit tracker", type="note", note_content="already set")
        assert result.note_content == "already set"

    def test_non_prefix_task_type_unchanged(self):
        result = _pp("buy groceries", type="task")
        assert result.type == "task"

    def test_note_type_without_prefix_unchanged(self):
        # LLM correctly returned note — don't interfere.
        result = _pp("packing list for the trip", type="note", note_content="- [ ] passport")
        assert result.type == "note"


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
