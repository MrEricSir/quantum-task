"""
Unit tests for card_sections.py — CardSection enum and BOARD_SECTIONS tuple.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from card_sections import CardSection, BOARD_SECTIONS


class TestCardSectionValues:
    """Enum values must match the strings stored in the DB."""

    def test_today_value(self):
        assert CardSection.TODAY == "today"

    def test_week_value(self):
        assert CardSection.WEEK == "week"

    def test_month_value(self):
        assert CardSection.MONTH == "month"

    def test_later_value(self):
        assert CardSection.LATER == "later"

    def test_none_value(self):
        assert CardSection.NONE == "none"

    def test_all_five_members(self):
        assert set(CardSection) == {
            CardSection.TODAY, CardSection.WEEK, CardSection.MONTH,
            CardSection.LATER, CardSection.NONE,
        }


class TestCardSectionIsStr:
    """CardSection is a str subclass — comparisons with bare strings work."""

    def test_equals_bare_string(self):
        assert CardSection.TODAY == "today"
        assert CardSection.NONE == "none"

    def test_in_set_of_strings(self):
        string_set = {"today", "week", "month", "later"}
        assert CardSection.TODAY in string_set
        assert CardSection.NONE not in string_set

    def test_usable_as_dict_key_with_string(self):
        d = {"today": 0, "week": 1, "month": 2, "later": 3}
        assert d[CardSection.TODAY] == 0
        assert d[CardSection.LATER] == 3

    def test_value_attribute(self):
        # Always use .value when embedding in strings (f-string behaviour
        # changed in Python 3.12+ for str+Enum; == comparison is the safe API)
        assert CardSection.TODAY.value == "today"
        assert CardSection.NONE.value == "none"


class TestBoardSections:
    """BOARD_SECTIONS excludes 'none' and is in display order."""

    def test_length(self):
        assert len(BOARD_SECTIONS) == 4

    def test_order(self):
        assert list(BOARD_SECTIONS) == [
            CardSection.TODAY, CardSection.WEEK,
            CardSection.MONTH, CardSection.LATER,
        ]

    def test_none_excluded(self):
        assert CardSection.NONE not in BOARD_SECTIONS

    def test_all_board_sections_present(self):
        assert CardSection.TODAY in BOARD_SECTIONS
        assert CardSection.WEEK  in BOARD_SECTIONS
        assert CardSection.MONTH in BOARD_SECTIONS
        assert CardSection.LATER in BOARD_SECTIONS
