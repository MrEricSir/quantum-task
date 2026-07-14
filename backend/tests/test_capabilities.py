"""
Tests for the shared capability prompt fragments (Level 1 architecture).

Verifies that:
1. Each capability module exports PARSE_DESCRIPTION and TELEGRAM_DESCRIPTION.
2. Capability descriptions are actually embedded in the assembled prompts
   (not silently dropped or overridden).
3. Related capabilities use consistent terminology across parse and Telegram surfaces.
"""

import pytest

import capabilities.food as food
import capabilities.habit_check as habit_check
import capabilities.mood as mood
import capabilities.task_complete as task_complete
from model_plugins.base import BASE_INSTRUCTIONS, BaseModelPlugin
from telegram.bot import _TELEGRAM_INTENT_PROMPT


# ── Capability module exports ──────────────────────────────────────────────────

ALL_CAPS = [food, mood, habit_check, task_complete]


@pytest.mark.parametrize("cap", ALL_CAPS, ids=["food", "mood", "habit_check", "task_complete"])
def test_exports_parse_description(cap):
    assert hasattr(cap, "PARSE_DESCRIPTION")
    assert isinstance(cap.PARSE_DESCRIPTION, str)
    assert len(cap.PARSE_DESCRIPTION) > 20


@pytest.mark.parametrize("cap", ALL_CAPS, ids=["food", "mood", "habit_check", "task_complete"])
def test_exports_telegram_description(cap):
    assert hasattr(cap, "TELEGRAM_DESCRIPTION")
    assert isinstance(cap.TELEGRAM_DESCRIPTION, str)
    assert len(cap.TELEGRAM_DESCRIPTION) > 20


# ── Parse prompt contains capabilities ────────────────────────────────────────

def _assembled_parse_prompt() -> str:
    """Build a fully-formatted parse system prompt via BaseModelPlugin."""
    plugin = BaseModelPlugin()
    return plugin.get_system_prompt(
        today="2026-07-14",
        weekday="Tuesday",
        tomorrow="2026-07-15",
        tags_section="",
    )


def test_food_parse_description_in_base_instructions():
    """food.PARSE_DESCRIPTION must be embedded in BASE_INSTRUCTIONS."""
    # A unique phrase from the food capability that shouldn't appear elsewhere
    assert "first- or second-person eating/drinking verb" in BASE_INSTRUCTIONS


def test_food_parse_description_in_assembled_prompt():
    prompt = _assembled_parse_prompt()
    assert "first- or second-person eating/drinking verb" in prompt
    assert 'set title to the food/drink description only' in prompt


def test_mood_parse_description_in_base_instructions():
    assert "drained/exhausted" in BASE_INSTRUCTIONS
    assert "great/energized" in BASE_INSTRUCTIONS


def test_mood_parse_description_in_assembled_prompt():
    prompt = _assembled_parse_prompt()
    assert "drained/exhausted" in prompt
    assert "If user gives N/5, use N directly" in prompt


def test_habit_check_parse_description_in_base_instructions():
    assert "habit_check" in BASE_INSTRUCTIONS
    assert "would this activity make sense as something done" in BASE_INSTRUCTIONS


def test_habit_check_parse_description_in_assembled_prompt():
    prompt = _assembled_parse_prompt()
    assert "habit_check" in prompt
    assert "went for a run" in prompt


def test_task_complete_parse_description_in_base_instructions():
    assert "task_complete" in BASE_INSTRUCTIONS
    assert "stripping the completion verb" in BASE_INSTRUCTIONS


def test_task_complete_parse_description_in_assembled_prompt():
    prompt = _assembled_parse_prompt()
    assert "task_complete" in prompt
    assert "stripping the completion verb" in prompt


# ── Telegram prompt contains capabilities ─────────────────────────────────────

def test_food_telegram_description_in_intent_prompt():
    assert "log_food" in _TELEGRAM_INTENT_PROMPT
    assert "meal_type" in _TELEGRAM_INTENT_PROMPT
    assert "raw_input" in _TELEGRAM_INTENT_PROMPT


def test_mood_telegram_description_in_intent_prompt():
    assert "log_mood" in _TELEGRAM_INTENT_PROMPT
    assert "drained/exhausted" in _TELEGRAM_INTENT_PROMPT
    # Guard that food/mood boundary is documented
    assert "NOT food" in _TELEGRAM_INTENT_PROMPT


def test_habit_check_telegram_description_in_intent_prompt():
    assert "complete_habit" in _TELEGRAM_INTENT_PROMPT
    assert "match_query" in _TELEGRAM_INTENT_PROMPT


def test_task_complete_telegram_description_in_intent_prompt():
    assert "mark_complete" in _TELEGRAM_INTENT_PROMPT


# ── Cross-surface consistency ─────────────────────────────────────────────────

def test_food_trigger_verbs_consistent_across_surfaces():
    """Both food descriptions should mention the same core trigger context."""
    for verb in ("had", "ate", "drank"):
        assert verb in food.PARSE_DESCRIPTION, f"'{verb}' missing from food.PARSE_DESCRIPTION"
        assert verb in food.TELEGRAM_DESCRIPTION, f"'{verb}' missing from food.TELEGRAM_DESCRIPTION"


def test_mood_energy_scale_consistent_across_surfaces():
    """Both mood descriptions should reference the same 1–5 scale anchors."""
    for anchor in ("drained/exhausted", "great/energized"):
        assert anchor in mood.PARSE_DESCRIPTION, f"'{anchor}' missing from mood.PARSE_DESCRIPTION"
        assert anchor in mood.TELEGRAM_DESCRIPTION, f"'{anchor}' missing from mood.TELEGRAM_DESCRIPTION"


def test_food_does_not_appear_in_mood_and_vice_versa():
    """Mood description must warn about food; food description must not mention mood."""
    assert "food" in mood.TELEGRAM_DESCRIPTION  # the NOT-food guard
    assert "mood" not in food.PARSE_DESCRIPTION
    assert "mood" not in food.TELEGRAM_DESCRIPTION
