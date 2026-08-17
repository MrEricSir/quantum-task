# Shared description of workout logging for LLM prompts, plus the actual parse
# implementation -- imported by routers/workouts.py (webapp) and telegram/bot.py
# (Telegram), so both surfaces get identical LLM enrichment. Mirrors capabilities/
# food.py's layout.

import json

from deps import llm_client, LLM_MODEL

WORKOUT_TYPES = {"run", "cycle", "row", "swim", "strength", "yoga", "sport", "other"}

_PARSE_SYSTEM = """\
You parse workout log entries into structured data.
Respond with ONLY valid JSON (no markdown, no explanation).

{
  "type":  "run" | "cycle" | "row" | "swim" | "strength" | "yoga" | "sport" | "other",
  "value": numeric measurement or null (e.g. 5000 for "rowed 5000m", 185 for "bench 185 lbs", 3.1 for "ran 3.1 miles"),
  "unit":  short unit string or null (e.g. "m", "km", "miles", "lbs", "kg", "min"),
  "notes": "one brief sentence describing the workout, or null"
}

Type selection rules:
- run: running, jogging, treadmill
- cycle: biking, cycling, spinning, Peloton
- row: rowing machine, erg, on-water rowing
- swim: swimming, pool laps
- strength: weight lifting, resistance training, gym, bench, squat, deadlift, dumbbells
- yoga: yoga, stretching, pilates, flexibility
- sport: basketball, tennis, soccer, golf, climbing, or any named sport
- other: anything that doesn't fit above

value/unit rules:
- Extract the primary measurement if present (distance, weight, reps, time)
- For strength, prefer the weight used (e.g. "bench pressed 185 lbs" → value=185, unit="lbs")
- For rowing/running/cycling, prefer distance (e.g. "rowed 5k" → value=5, unit="km")
- For yoga/sport/other with only time given, use minutes
- If no measurement is present, set both to null
- IMPORTANT: Never convert units. Preserve the exact value and unit the user provided.
  If the user says "1 mi", output value=1, unit="mi" — do not convert to meters or any other unit.
"""


def parse_workout(raw: str) -> dict:
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _PARSE_SYSTEM},
                {"role": "user",   "content": raw},
            ],
            max_tokens=150,
        )
        data = json.loads(resp.choices[0].message.content.strip())
        wtype = data.get("type", "other")
        if wtype not in WORKOUT_TYPES:
            wtype = "other"
        value = data.get("value")
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        unit = data.get("unit") or None
        if unit:
            unit = str(unit)[:20]
        notes = data.get("notes") or None
        return {"type": wtype, "value": value, "unit": unit, "notes": notes}
    except Exception:
        return {"type": "other", "value": None, "unit": None, "notes": None}


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

# Embedded in _TELEGRAM_INTENT_PROMPT as a top-level action block.
TELEGRAM_DESCRIPTION = """\
  "log_workout"
      User is logging a physical workout or exercise session (past, present, or
      imminent). Trigger on activity verbs: ran, rowed, cycled, swam, lifted,
      worked out, did yoga, played (sport), went for a (run/ride/swim), etc.
      Also return:
        "raw_input" — exact workout description from the user's message
      Examples: "ran 5 miles", "rowed 5000m", "did yoga for 30 min",
                "bench pressed 185 lbs", "played tennis", "went for a bike ride"\
"""
