# Shared description of mood/energy logging for LLM prompts.
# Imported by model_plugins/base.py (parse flow) and telegram/bot.py (intent flow).

# Embedded in BASE_INSTRUCTIONS under the `type` field breakdown.
# First line has no leading indent (the caller supplies the 18-space prefix).
# Continuation lines carry 26-space indent to align with the rest of the block.
PARSE_DESCRIPTION = """\
mood  = logging current energy or mood level
                          Use when the input describes how the user is feeling or their energy.
                          Examples: "feeling great today", "pretty tired 3/5", "energy level 4",
                          "low energy today", "feeling focused and productive", "exhausted"
                          Set title to a short description (e.g. "Good energy", "Feeling drained")
                          Set energy to an integer 1\u20135:
                            1 = drained/exhausted  2 = tired/low  3 = okay/neutral
                            4 = good/focused       5 = great/energized
                          If user gives N/5, use N directly. If N/10, round to nearest (N+1)/2.\
"""

# Embedded in _TELEGRAM_INTENT_PROMPT as a top-level action block.
TELEGRAM_DESCRIPTION = """\
  "log_mood"
      User is logging their current energy or mood level \u2014 NOT food.
      Also return:
        "energy" \u2014 integer 1\u20135 (1=drained/exhausted, 2=tired/low, 3=okay/neutral,
                   4=good/focused, 5=great/energized/amazing)
        "note"   \u2014 brief descriptor extracted from the message, or null
      If user gives N/5, use N directly. If N/10, convert to nearest 1\u20135.
      IMPORTANT: Do NOT use this for food or drink. If the user mentions food they ate
      or drank, use log_food instead.
      Examples: "feeling great today", "pretty tired 3/5", "energy 4", "exhausted",
                "low energy today", "feeling focused and productive"\
"""
