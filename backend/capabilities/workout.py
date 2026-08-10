# Shared description of workout logging for LLM prompts.
# Imported by model_plugins/base.py (parse flow) and telegram/bot.py (intent flow).

PARSE_DESCRIPTION = """\
workout = logging a physical workout or exercise session
                          Use when the user describes performing exercise in first person
                          (past, present, or imminent). Trigger on activity verbs:
                          ran, run, running, rowed, row, cycled, biked, swam, lifted,
                          worked out, did yoga, played (sport), went for a (run/ride/swim), etc.
                          Examples: "ran 5 miles", "rowed 5000m", "did yoga for 30 min",
                          "bench pressed 185 lbs", "played tennis", "went for a bike ride"
                          Do NOT use for habits like "run every morning" — that is type=habit.
                          Do NOT use for goal-setting like "run a marathon" — that is type=task.
                          When type is "workout", set title to a brief workout description\
"""

# Embedded in _TELEGRAM_INTENT_PROMPT as a top-level action block. Extracts the
# same structured type/value/unit fields routers/workouts.py's own _parse_workout
# LLM call would, but in the single Telegram intent-classification pass rather
# than a second round-trip -- matches how log_food skips food.py's separate
# quality-scoring call and stores directly from the intent fields.
TELEGRAM_DESCRIPTION = """\
  "log_workout"
      User is logging a physical workout or exercise session (past, present, or
      imminent). Trigger on activity verbs: ran, rowed, cycled, swam, lifted,
      worked out, did yoga, played (sport), went for a (run/ride/swim), etc.
      Also return:
        "raw_input" — exact workout description from the user's message
        "type"  — "run" | "cycle" | "row" | "swim" | "strength" | "yoga" | "sport" | "other"
        "value" — numeric measurement or null (e.g. 5000 for "rowed 5000m", 185 for "bench 185 lbs")
        "unit"  — short unit string or null (e.g. "m", "km", "miles", "lbs", "min") — never
                  convert units, preserve exactly what the user said
      Type selection: run=running/jogging/treadmill, cycle=biking/cycling/spinning,
      row=rowing machine/erg, swim=swimming/pool laps, strength=weights/gym/bench/squat/deadlift,
      yoga=yoga/stretching/pilates, sport=basketball/tennis/soccer/golf/any named sport,
      other=anything that doesn't fit above.
      Examples: "ran 5 miles", "rowed 5000m", "did yoga for 30 min",
                "bench pressed 185 lbs", "played tennis", "went for a bike ride"\
"""
