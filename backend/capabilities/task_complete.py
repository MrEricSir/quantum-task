# Shared description of one-time task completion for LLM prompts.
# Imported by model_plugins/base.py (parse flow) and telegram/bot.py (intent flow).

# Embedded in BASE_INSTRUCTIONS under the `type` field breakdown.
# First line has no leading indent (the caller supplies the 18-space prefix).
# Continuation lines carry 26-space indent to align with the rest of the block.
PARSE_DESCRIPTION = """\
task_complete = marking a one-time task as finished/archived
                          Use when the input marks a specific, non-recurring task as done:
                          (e.g. "finished the dentist appointment", "completed the report",
                          "done with the meeting", "archive the project proposal",
                          "I finished the oil change")
                          Set title to the task name, stripping the completion verb.\
"""

# Embedded in _TELEGRAM_INTENT_PROMPT as a top-level action block.
TELEGRAM_DESCRIPTION = """\
  "mark_complete"
      User is marking an existing task done.
      Also return "match_query": the task title or fragment to match.
      Examples: "done with dentist", "finished the report", "mark groceries complete"\
"""
