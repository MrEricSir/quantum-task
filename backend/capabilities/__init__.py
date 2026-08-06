# Shared LLM capability descriptions.
# Each module exports PARSE_DESCRIPTION (for model_plugins/base.py)
# and TELEGRAM_DESCRIPTION (for telegram/bot.py), so both prompts
# stay in sync from a single source of truth. registry.py builds the
# typed Operation registry both files look these up through.
from capabilities import food, habit_check, mood, task_complete, workout, registry

__all__ = ["food", "habit_check", "mood", "task_complete", "workout", "registry"]
