# Shared LLM capability descriptions.
# Each module exports PARSE_DESCRIPTION (for model_plugins/base.py)
# and TELEGRAM_DESCRIPTION (for telegram/bot.py), so both prompts
# stay in sync from a single source of truth.
from capabilities import food, habit_check, mood, task_complete

__all__ = ["food", "habit_check", "mood", "task_complete"]
