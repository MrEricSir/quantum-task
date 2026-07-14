# Shared description of habit completion for LLM prompts.
# Imported by model_plugins/base.py (parse flow) and telegram/bot.py (intent flow).

# Embedded in BASE_INSTRUCTIONS under the `type` field breakdown.
# First line has no leading indent (the caller supplies the 18-space prefix).
# Continuation lines carry 26-space indent to align with the rest of the block.
PARSE_DESCRIPTION = """\
habit_check = marking an existing habit as completed today
                          Use when the input describes a recurring habit in the past tense,
                          whether or not it uses an explicit completion verb.
                          With completion verb: "did my meditation", "completed my morning run",
                          "finished my walk", "checked off yoga", "done with exercise"
                          Natural past tense (NO completion verb): "talked to a stranger",
                          "went for a run", "meditated this morning", "journaled for 10 minutes",
                          "practiced guitar", "walked the dog"
                          Key question: would this activity make sense as something done
                          repeatedly/daily? If yes \u2192 habit_check, even without "did/finished".
                          When type is "habit_check", set title to the base habit name
                          (e.g. "did my meditation" \u2192 "Meditation";
                           "talked to a stranger" \u2192 "Talk to a stranger";
                           "went for a run" \u2192 "Run")
                          Do NOT use for clearly one-time tasks: "emailed the quarterly report",
                          "booked a flight to NYC", "sent the invoice to the client"\
"""

# Embedded in _TELEGRAM_INTENT_PROMPT as a top-level action block.
TELEGRAM_DESCRIPTION = """\
  "complete_habit"
      User is marking a habit done for today. Use this when the thing being completed
      matches one of the available habits, not a task.
      Also return "match_query": the habit name or fragment to match.
      Examples: "done meditation", "finished yoga", "did my workout", "mark reading complete",
                "exercise done", "I meditated"\
"""
