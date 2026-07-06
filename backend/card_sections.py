"""
Typed constants for the Card.section field.

The ``section`` column is stored as a plain String in SQLite.  This module
provides a Python-level ``str`` enum so callers can compare against named
constants rather than bare string literals, and IDEs / linters can catch typos.

Valid values:
    today  — shown in the "Today" board column
    week   — "This Week" column
    month  — "This Month" column
    later  — "Stash" column
    none   — reference card; shown only on the Cards page, never on the board
"""

import enum


class CardSection(str, enum.Enum):
    TODAY = "today"
    WEEK  = "week"
    MONTH = "month"
    LATER = "later"
    NONE  = "none"   # reference card


# Convenience tuple of board sections (excludes NONE).
BOARD_SECTIONS = (
    CardSection.TODAY,
    CardSection.WEEK,
    CardSection.MONTH,
    CardSection.LATER,
)
