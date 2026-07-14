"""
Base class for model-specific parse plugins.

Each plugin encapsulates everything that varies between Ollama models:
  - The model identifier string
  - Prompt examples tuned for that model's tendencies
  - Raw-JSON normalization (pre-Pydantic)
  - Parsed-result post-processing (post-Pydantic)

To add a new model:
  1. Create model_plugins/<your_model>.py subclassing BaseModelPlugin
  2. Register it in model_plugins/__init__.py
"""

from __future__ import annotations
import re
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any

import capabilities.food as _food
import capabilities.habit_check as _habit_check
import capabilities.mood as _mood
import capabilities.task_complete as _task_complete

# ── Shared prompt instructions ────────────────────────────────────────────────
# Examples are intentionally excluded here — each plugin supplies its own.
# Capability descriptions (food, mood, habit_check, task_complete) are imported
# from capabilities/ so that parse-flow and Telegram stay in sync.

BASE_INSTRUCTIONS = f"""\
You parse natural language into structured todo items. Reply only with valid JSON. No explanation.

CRITICAL: You are a parser, not a content generator. Never write poems, stories, essays, lists,
recipes, or any other creative or informational content in response to a task description.
"write a poem", "draft an email", "compose a speech" are TASKS to be done by the user —
extract them as tasks with that title. Do not produce the content itself.\


Reference dates:
  Today    : {{today}} ({{weekday}})
  Tomorrow : {{tomorrow}}

{{tags_section}}

Fields:
  type          — "task" | "habit" | "goal" | "food" | "habit_check" | "task_complete" | "assist"
                  task  = a discrete, completable item with a clear done state
                          (e.g. "send Bob the report", "dentist appointment", "buy groceries")
                  habit = something you do repeatedly on an ongoing, indefinite basis with
                          no specific end (e.g. "exercise every morning", "meditate daily",
                          "journal every night", "drink 8 glasses of water each day")
                  goal  = setting or updating a health metric target, NOT a recurring habit
                          Use when the input explicitly says "set/change/update my X goal to Y"
                          or "my X goal is Y" (e.g. "set my weight goal to 75 kg")
                          Always set withings_metric and withings_goal when type="goal"
                  {_food.PARSE_DESCRIPTION}
                  {_habit_check.PARSE_DESCRIPTION}
                  {_task_complete.PARSE_DESCRIPTION}
                  assist = a conversational or planning request — NOT a specific item to capture
                          Use when the input is a question, request for help, or anything that
                          does not map cleanly to a task, habit, food log, or completion.
                          Examples: "help me plan my week", "what should I focus on today?",
                          "can you suggest tasks for my project", "how should I prioritize?"
                          Do NOT use for imperative statements like "call dentist" or
                          "meditate daily" — those are tasks/habits even if phrased as requests.
                  {_mood.PARSE_DESCRIPTION}
  title         — task or habit name; preserve names, people, and key context from
                  the input; only strip date/time phrases; do NOT paraphrase or summarize
  description   — verbatim extra context or content from the user's input; null if none;
                  NEVER generate, compose, or invent content here.
  section       — "today" | "week" | "month" | "later"
                    later  = DEFAULT for tasks; use when no date or deadline is mentioned
                    today  = explicitly due today
                    week   = due in 1-7 days (tomorrow, this week, this Friday, next few days)
                    month  = due in 8-30 days (in two weeks, in three weeks, next month)
                    IMPORTANT: if no deadline is stated for a task, always use "later", never "week"
  scheduled_at  — ISO 8601 datetime string (YYYY-MM-DDTHH:MM:SS) only when the input
                  contains an explicit clock time (e.g. "at 3pm", "at noon", "morning",
                  "evening"). null if no time-of-day is mentioned at all.
                  If section is "later", scheduled_at MUST be null.
                  NEVER put natural language here — always resolve to a real date.
                  Common times: noon=12:00:00, midnight=00:00:00, morning=09:00:00,
                  afternoon=14:00:00, evening=18:00:00
  suggested_tags  — list of applicable tag names from the available tags; [] if none fit
  clarification_question — a short clarifying question for the user IF and ONLY IF the type
                  is genuinely ambiguous (e.g. could be task or habit). null in all other cases.
                  Example: "Did you mean to track this as a recurring habit?"
  recurrence_rule — "daily" | "weekly" | "monthly" | "yearly" | null
                    Set only when the input explicitly describes a repeating task:
                    "every day" / "daily" / "each morning"  → "daily"   (section: "today")
                    "every week" / "weekly" / "every Monday" → "weekly"  (section: "week")
                    "every month" / "monthly"                → "monthly" (section: "month")
                    "every year" / "yearly" / "annually"     → "yearly"  (section: "later")
                    null if no recurrence is mentioned
                    When set, section must reflect the cadence above, not "later"
  withings_metric — ONLY for habits: "steps" | "fat_ratio" | "weight" | null
                    Set when the habit explicitly mentions one of these health metrics.
                    "steps" for step-count goals ("10,000 steps a day", "walk 5k steps")
                    "fat_ratio" for body fat percentage ("body fat under 20%")
                    "weight" for body weight ("weigh less than 75 kg")
                    null for all other habits and all tasks
  withings_goal   — ONLY for habits with withings_metric: numeric goal (float) or null
                    steps: target steps per day (e.g. 10000)
                    fat_ratio: max body fat % to stay at or below (e.g. 20.0)
                    weight: max weight in kg to stay at or below (e.g. 75.0)\
"""


BULK_SUFFIX = """\

IMPORTANT — MULTIPLE ITEMS:
- The input may contain multiple todos separated by commas, periods, "and", or newlines.
- Parse EACH distinct action or event as its own item in the array.
- EXCEPTION: if the input is clearly a shopping/grocery list — e.g. "buy X, Y, and Z" \
with 3+ items, or a store name followed by food items on separate lines — group ALL of \
them into ONE item with type="task", section="later", and each item on its own line in description.
- For each item, include "source_text": the verbatim fragment of the user's input that this item was parsed from (copy the exact words, do not paraphrase).
Return ONLY valid JSON: {{"items": [<ParsedCard>, ...]}} — no prose, no explanation.\
"""

# Weekday name → weekday index (Monday=0)
_ISO_IN_TITLE_RE = re.compile(r'\s*\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\b')

_WEEKDAY_IDX: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_WEEKDAY_RE = re.compile(
    r'\b(?:next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
    re.I,
)

_TIME_RE = re.compile(
    r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b'
    r'|\b(noon|midnight|morning|afternoon|evening)\b'
    r'|\bat\s+(\d{1,2})(?::(\d{2}))?\b',  # bare "at N" with no am/pm
    re.I,
)

_TIME_WORDS: dict[str, dt_time] = {
    "noon":      dt_time(12, 0),
    "midnight":  dt_time(0, 0),
    "morning":   dt_time(9, 0),
    "afternoon": dt_time(14, 0),
    "evening":   dt_time(18, 0),
}

# ── Goal-setting detection ────────────────────────────────────────────────────
# Matches explicit intent to SET or CHANGE a health goal (vs. creating a habit)
_GOAL_SET_RE = re.compile(
    r'\b(?:set|change|update)\b.{0,25}\bgoal\b'  # "set my weight goal"
    r'|\bmy\b.{0,15}\bgoal\b\s+is\b'             # "my weight goal is 75"
    r'|\bgoal\b\s*[:\=]',                         # "goal: 75 kg" / "goal = 10000"
    re.I,
)

# ── Health metric detection regexes ───────────────────────────────────────────
# Steps: "5,000 steps", "10k steps", "10,000 steps a day"
_STEPS_K_RE  = re.compile(r'\b(\d+(?:\.\d+)?)\s*k\s*steps?\b', re.I)
_STEPS_NUM_RE = re.compile(r'\b([\d,]+)\s*steps?\b', re.I)
# Goal context: "step goal to 10,000" (number follows "step(s) goal")
_STEPS_GOAL_RE = re.compile(r'\bsteps?\s+goal\b.{0,40}?([\d,]+)', re.I)

# Body fat %: "body fat under 20%", "20% body fat", "fat ratio 20%"
_FAT_RE = re.compile(
    r'\bbody\s*fat\b[^%\d]*(\d+(?:\.\d+)?)\s*%'
    r'|(\d+(?:\.\d+)?)\s*%\s*(?:body\s*)?fat\b'
    r'|\bfat\s+ratio\b[^%\d]*(\d+(?:\.\d+)?)\s*%',
    re.I,
)

# Weight: "75 kg", "165 lbs / pounds"
_WEIGHT_KG_RE = re.compile(r'\b(\d+(?:\.\d+)?)\s*kg\b', re.I)
_WEIGHT_LB_RE = re.compile(r'\b(\d+(?:\.\d+)?)\s*(?:lbs?|pounds?)\b', re.I)


def resolve_dates(parsed: Any, *, text: str, today: date) -> Any:
    """
    Post-parse hook: resolve relative date phrases in `text` to concrete
    dates in `parsed.scheduled_at`.  Only fills scheduled_at when it is
    currently None (respects any time the LLM already resolved).

    Handles:
      - "today"            → today at noon
      - "tomorrow"         → tomorrow at noon
      - "(next) <weekday>" → nearest future occurrence at noon
    When a time is mentioned alongside the phrase, uses that time instead of noon.
    """
    lowered = text.strip().lower()

    # Determine the time-of-day from the original input (if any).
    def _extract_time() -> dt_time:
        m = _TIME_RE.search(lowered)
        if not m:
            return dt_time(12, 0)  # default: noon
        if m.group(4):                          # named word: noon, morning, …
            return _TIME_WORDS[m.group(4).lower()]
        if m.group(5) is not None:              # bare "at N" — assume pm for 1–11
            hour = int(m.group(5))
            minute = int(m.group(6)) if m.group(6) else 0
            if 1 <= hour <= 11:
                hour += 12
            return dt_time(hour, minute)
        hour = int(m.group(1))                  # explicit am/pm
        minute = int(m.group(2)) if m.group(2) else 0
        meridiem = m.group(3).lower()
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return dt_time(hour, minute)

    if parsed.scheduled_at is not None:
        return parsed

    target_date: date | None = None

    if re.search(r'\btoday\b', lowered):
        target_date = today
    elif re.search(r'\btomorrow\b', lowered):
        target_date = today + timedelta(days=1)
    else:
        wm = _WEEKDAY_RE.search(lowered)
        if wm:
            target_wd = _WEEKDAY_IDX[wm.group(1).lower()]
            current_wd = today.weekday()
            days_ahead = (target_wd - current_wd) % 7 or 7  # always future
            target_date = today + timedelta(days=days_ahead)
        elif _TIME_RE.search(lowered):
            # Bare time phrase with no date → assume today
            target_date = today

    if target_date is not None:
        t = _extract_time()
        parsed.scheduled_at = datetime.combine(target_date, t)
        # Fix up section to match the resolved date
        if parsed.section == "later":
            delta = (target_date - today).days
            if delta <= 0:
                parsed.section = "today"
            elif delta <= 7:
                parsed.section = "week"
            elif delta <= 30:
                parsed.section = "month"

    return parsed


class BaseModelPlugin:
    """
    Default plugin — usable as a pass-through fallback for any model not
    explicitly registered.  Subclasses override only what they need.
    """

    # Ollama model identifier, e.g. "phi4-mini", "llama3.2"
    model_name: str = ""

    # Prompt examples as (input_text, output_json_template) pairs.
    # output_json_template may contain {today} and {tomorrow} placeholders.
    # Use {{ / }} for literal braces inside the JSON string.
    EXAMPLES: list[tuple[str, str]] = []

    # Examples specifically for the bulk prompt. Same format but output must
    # be wrapped in {"items": [...]}.
    BULK_EXAMPLES: list[tuple[str, str]] = []

    # ── Prompt ────────────────────────────────────────────────────────────────

    def get_system_prompt(
        self,
        *,
        today: str,
        weekday: str,
        tomorrow: str,
        tags_section: str,
    ) -> str:
        body = BASE_INSTRUCTIONS.format(
            today=today,
            weekday=weekday,
            tomorrow=tomorrow,
            tags_section=tags_section,
        )
        examples = self._build_examples(today, tomorrow)
        return body + ("\n\n" + examples if examples else "")

    def get_bulk_system_prompt(
        self,
        *,
        today: str,
        weekday: str,
        tomorrow: str,
        tags_section: str,
    ) -> str:
        # Full field definitions + model-specific examples + bulk wrapper
        body = BASE_INSTRUCTIONS.format(
            today=today,
            weekday=weekday,
            tomorrow=tomorrow,
            tags_section=tags_section,
        )
        examples = self._build_examples(today, tomorrow)
        bulk_examples = self._build_bulk_examples(today, tomorrow)
        return (body
                + ("\n\n" + examples if examples else "")
                + BULK_SUFFIX
                + ("\n\n" + bulk_examples if bulk_examples else ""))

    def _build_examples(self, today: str, tomorrow: str) -> str:
        if not self.EXAMPLES:
            return ""
        lines = [f"Examples (today is {today}, tomorrow is {tomorrow}):"]
        for input_text, json_tmpl in self.EXAMPLES:
            json_str = json_tmpl.format(today=today, tomorrow=tomorrow)
            lines.append(f'  Input : "{input_text}"')
            lines.append(f'  Output: {json_str}')
        return "\n".join(lines)

    def _build_bulk_examples(self, today: str, tomorrow: str) -> str:
        if not self.BULK_EXAMPLES:
            return ""
        lines = [f"Bulk examples (today is {today}, tomorrow is {tomorrow}):"]
        for input_text, json_tmpl in self.BULK_EXAMPLES:
            json_str = json_tmpl.format(today=today, tomorrow=tomorrow)
            lines.append(f'  Input : "{input_text}"')
            lines.append(f'  Output: {json_str}')
        return "\n".join(lines)

    # ── Normalization hooks ───────────────────────────────────────────────────

    # Maps common LLM section hallucinations to valid values
    _SECTION_ALIASES = {
        "tomorrow": "week",
        "this week": "week",
        "next week": "week",
        "this month": "month",
        "next month": "month",
        "someday": "later",
        "future": "later",
    }
    _VALID_SECTIONS = {"today", "week", "month", "later"}

    _VALID_RECURRENCES = {"daily", "weekly", "monthly", "yearly"}
    _RECURRENCE_ALIASES = {
        "week": "weekly", "day": "daily", "month": "monthly", "year": "yearly",
        "annual": "yearly", "annually": "yearly",
    }

    _VALID_TYPES = {"task", "habit", "goal", "assist", "food", "habit_check", "task_complete", "mood"}

    def normalize_raw(self, raw: dict) -> dict:
        """
        Called on the raw dict from json.loads() before Pydantic validation.
        Use this to fix structural issues — wrong types, malformed values, etc.
        """
        # Some models wrap their output in an array — unwrap it.
        if isinstance(raw, list):
            raw = raw[0] if raw else {}

        # If the LLM embedded an ISO datetime inside the title, rescue it into scheduled_at
        if isinstance(raw.get("title"), str):
            m = _ISO_IN_TITLE_RE.search(raw["title"])
            if m:
                if not raw.get("scheduled_at"):
                    raw["scheduled_at"] = m.group(1)
                cleaned = _ISO_IN_TITLE_RE.sub("", raw["title"]).strip(" -:,")
                # If stripping left the title empty, promote description to title
                if not cleaned and isinstance(raw.get("description"), str) and raw["description"]:
                    cleaned = raw.pop("description")
                raw["title"] = cleaned

        # Common field-name hallucinations for "title"
        _KNOWN_FIELDS = {
            "type", "title", "description", "section", "scheduled_at",
            "suggested_tags", "recurrence_rule", "clarification_question",
        }
        if "title" not in raw:
            for alias in ("text", "name", "task", "content", "item", "summary", "label", "todo"):
                if alias in raw and isinstance(raw[alias], str):
                    raw["title"] = raw.pop(alias)
                    break
            else:
                # Last resort: join all unrecognised string values into a title
                parts = [v for k, v in raw.items() if k not in _KNOWN_FIELDS and isinstance(v, str)]
                if parts:
                    raw["title"] = " ".join(parts)

        type_val = str(raw.get("type", "task")).strip().lower()
        if type_val == "note":
            # Old LLM response using the previous schema — convert to a stash task.
            # Preserve note_content (if present) in description so no text is lost.
            raw["type"] = "task"
            raw["section"] = "later"
            if not raw.get("description") and raw.get("note_content"):
                raw["description"] = raw["note_content"]
        elif type_val in self._VALID_TYPES:
            raw["type"] = type_val
            # Ensure a non-empty title for assist items (other fields unused by frontend)
            if type_val == "assist" and not raw.get("title"):
                raw["title"] = "Assist"
            # Normalise energy (1-5) for mood entries — LLM may return a string or float
            if type_val == "mood":
                try:
                    raw["energy"] = max(1, min(5, int(float(str(raw.get("energy") or "3")))))
                except (ValueError, TypeError):
                    raw["energy"] = 3
                if not raw.get("title"):
                    raw["title"] = {1: "Drained", 2: "Low energy", 3: "Okay",
                                    4: "Good energy", 5: "Energized"}[raw["energy"]]
        else:
            raw["type"] = "task"

        section = raw.get("section", "")
        if isinstance(section, str):
            normalized = section.strip().lower()
            if normalized not in self._VALID_SECTIONS:
                raw["section"] = self._SECTION_ALIASES.get(normalized, "later")
        rule = raw.get("recurrence_rule")
        if rule is not None:
            normalized = str(rule).strip().lower()
            if normalized not in self._VALID_RECURRENCES:
                raw["recurrence_rule"] = self._RECURRENCE_ALIASES.get(normalized, None)

        # Coerce any non-string value in nullable string fields to None
        for field in ("description", "scheduled_at", "clarification_question"):
            val = raw.get(field)
            if val is not None and not isinstance(val, str):
                raw[field] = None

        return raw

    _RECURRENCE_SECTION = {
        "daily": "today",
        "weekly": "week",
        "monthly": "month",
        "yearly": "later",
    }

    # Phrase patterns that must map to a specific section regardless of LLM output.
    # Checked against the lowercased original user input.
    _SECTION_OVERRIDES: list[tuple[re.Pattern, str]] = [
        (re.compile(r'\bnext week\b'),    "week"),
        (re.compile(r'\bthis week\b'),    "week"),
        (re.compile(r'\btomorrow\b'),     "week"),
        (re.compile(r'\bin a few days\b'), "week"),
        (re.compile(r'\bthis (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b'), "week"),
        (re.compile(r'\bnext month\b'),   "month"),
        (re.compile(r'\bin two weeks\b'), "month"),
        (re.compile(r'\bin three weeks\b'), "month"),
    ]

    # "add a habit to X" / "create a habit for X" → type=habit, title=X
    _ADD_HABIT_RE = re.compile(
        r'^(?:add|create|make|start|set up|track)\s+(?:a\s+|an\s+|the\s+)?habit\s+(?:to\s+|of\s+|for\s+|about\s+)?(.+)$',
        re.I,
    )

    def post_process(self, parsed: Any, *, text: str = "") -> Any:
        """
        Called on the validated ParsedCard after Pydantic.
        `text` is the original user input — use it to check what was actually stated.
        """
        # Assist and mood items need no further processing.
        if parsed.type in ("assist", "mood"):
            return parsed

        lowered = text.strip().lower()

        # Enforce section from explicit temporal phrases in the input text.
        # This catches cases where the LLM correctly understands the phrase but
        # maps it to the wrong section bucket.
        for pattern, forced_section in self._SECTION_OVERRIDES:
            if pattern.search(lowered):
                parsed.section = forced_section
                break

        # "add a habit to X" → convert to habit with title X
        m = self._ADD_HABIT_RE.match(lowered)
        if m:
            parsed.type = "habit"
            parsed.title = m.group(1).strip().capitalize()
            if not parsed.recurrence_rule:
                parsed.recurrence_rule = "daily"
            if parsed.section == "later":
                parsed.section = self._RECURRENCE_SECTION.get(parsed.recurrence_rule, "today")

        # Recurring tasks default to "later" from the LLM because there's no
        # specific deadline — override with a section that matches the cadence.
        if parsed.recurrence_rule and parsed.section == "later":
            parsed.section = self._RECURRENCE_SECTION.get(parsed.recurrence_rule, "week")

        # Deterministically detect Withings health metrics.
        # Applies to habits and to goal-setting phrases (type may still be "task" from the LLM).
        _detect_metric = parsed.type == "habit" or bool(_GOAL_SET_RE.search(lowered))

        if _detect_metric and not parsed.withings_metric:
            # Steps — try "Nk steps", then "N steps", then "step goal to N" (goal context)
            km = _STEPS_K_RE.search(lowered)
            if km:
                parsed.withings_metric = "steps"
                parsed.withings_goal = float(km.group(1)) * 1000
            else:
                sm = _STEPS_NUM_RE.search(lowered) or _STEPS_GOAL_RE.search(lowered)
                if sm:
                    parsed.withings_metric = "steps"
                    parsed.withings_goal = float(sm.group(1).replace(",", ""))

        if _detect_metric and not parsed.withings_metric:
            # Body fat %
            fm = _FAT_RE.search(lowered)
            if fm:
                goal_str = fm.group(1) or fm.group(2) or fm.group(3)
                parsed.withings_metric = "fat_ratio"
                parsed.withings_goal = float(goal_str)

        if _detect_metric and not parsed.withings_metric:
            # Weight — kg first, then lbs (converted)
            wm = _WEIGHT_KG_RE.search(lowered)
            if wm:
                parsed.withings_metric = "weight"
                parsed.withings_goal = float(wm.group(1))
            else:
                lm = _WEIGHT_LB_RE.search(lowered)
                if lm:
                    parsed.withings_metric = "weight"
                    parsed.withings_goal = round(float(lm.group(1)) * 0.453592, 1)

        # Override type to "goal" when the input is explicitly about setting a health target.
        # This runs after metric detection so withings_metric is already populated.
        if parsed.withings_metric and parsed.withings_goal is not None and _GOAL_SET_RE.search(lowered):
            parsed.type = "goal"

        return parsed
