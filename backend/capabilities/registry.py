# Central registry of app "capabilities" -- operations that exist as both a
# capture-flow ParsedCard.type and (optionally) a Telegram intent action, so
# model_plugins/base.py and telegram/bot.py can look them up from one place
# instead of hand-maintaining parallel lists that can silently drift apart.
#
# Construction follows model_plugins/__init__.py's existing pattern (a plain
# list built here, not each module self-registering) rather than the
# self-registration sketch in ARCHITECTURE_FUTURE.md -- a capability module
# importing this file back would risk a real circular import once
# telegram_handler wiring is involved (see set_telegram_handler below).
#
# To add a new capability:
#   1. Create capabilities/<name>.py with PARSE_DESCRIPTION (and
#      TELEGRAM_DESCRIPTION if it should be reachable from Telegram too)
#   2. Add an Operation entry below
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional

import capabilities.food as food
import capabilities.habit_check as habit_check
import capabilities.mood as mood
import capabilities.task_complete as task_complete
import capabilities.workout as workout


@dataclass
class Operation:
    name: str                                  # ParsedCard.type value / capabilities/<name>.py module name
    telegram_action: Optional[str]             # Telegram intent "action" value, or None if not exposed
    parse_description: str
    telegram_description: Optional[str]
    surfaces: frozenset[str]                   # e.g. {"capture", "telegram"}
    telegram_handler: Optional[Callable[[dict, int, str], str]] = None


_operations = [
    Operation("food", "log_food", food.PARSE_DESCRIPTION, food.TELEGRAM_DESCRIPTION,
              frozenset({"capture", "telegram"})),
    Operation("habit_check", "complete_habit", habit_check.PARSE_DESCRIPTION, habit_check.TELEGRAM_DESCRIPTION,
              frozenset({"capture", "telegram"})),
    Operation("mood", "log_mood", mood.PARSE_DESCRIPTION, mood.TELEGRAM_DESCRIPTION,
              frozenset({"capture", "telegram"})),
    Operation("task_complete", "mark_complete", task_complete.PARSE_DESCRIPTION, task_complete.TELEGRAM_DESCRIPTION,
              frozenset({"capture", "telegram"})),
    Operation("workout", "log_workout", workout.PARSE_DESCRIPTION, workout.TELEGRAM_DESCRIPTION,
              frozenset({"capture", "telegram"})),
]

REGISTRY: dict[str, Operation] = {op.name: op for op in _operations}

_BY_TELEGRAM_ACTION: dict[str, Operation] = {
    op.telegram_action: op for op in REGISTRY.values() if op.telegram_action
}


def by_telegram_action(action: str) -> Optional[Operation]:
    return _BY_TELEGRAM_ACTION.get(action)


def set_telegram_handler(name: str, handler: Callable[[dict, int, str], str]) -> None:
    """Attach a Telegram handler after telegram/bot.py's handler functions
    are defined. Called from telegram/bot.py itself, once per capability
    with a telegram_action -- keeps this module from ever importing
    telegram.bot, which would be a real circular import (telegram/bot.py
    already imports this module for REGISTRY/by_telegram_action)."""
    REGISTRY[name].telegram_handler = handler
