# Shared description of food/drink logging for LLM prompts.
# Imported by model_plugins/base.py (parse flow) and telegram/bot.py (intent flow).

# Embedded in BASE_INSTRUCTIONS under the `type` field breakdown.
# First line has no leading indent (the caller supplies the 18-space prefix).
# Continuation lines carry 26-space indent to align with the rest of the block.
PARSE_DESCRIPTION = """\
food  = logging something eaten or drunk (past, present, or imminent)
                          Trigger on ANY first- or second-person eating/drinking verb:
                          ate, eat, eating, had, have, having, drank, drink, drinking,
                          consumed, grabbed, picked up, ordered, finished, just had, etc.
                          Examples: "I ate a donut", "had a cup of yogurt",
                          "ate sugar-free yogurt", "just had coffee", "drinking a beer",
                          "had a chicken salad for lunch", "grabbed a snack"
                          Do NOT classify food as a task or goal just because it mentions
                          a health attribute (e.g. "sugar-free", "low-cal", "protein shake").
                          When type is "food", set title to the food/drink description only\
"""

# Embedded in _TELEGRAM_INTENT_PROMPT as a top-level action block.
TELEGRAM_DESCRIPTION = """\
  "log_food"
      User is logging something they ate or drank. Use this whenever food or drink
      is mentioned in a past-tense or "I had / I ate / I drank" context.
      Also return:
        "raw_input" — exact food/drink description from the user's message
        "meal_type" — "breakfast" | "lunch" | "dinner" | "snack" | "drink" | null
      Examples: "I had yogurt and iced tea for breakfast", "just ate a salad for lunch",
                "had a coffee", "ate a muffin", "grabbed a green smoothie"\
"""
