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
from datetime import time as dt_time
from typing import Any

# ── Shared prompt instructions ────────────────────────────────────────────────
# Examples are intentionally excluded here — each plugin supplies its own.

BASE_INSTRUCTIONS = """\
You parse natural language into structured todo items. Reply only with valid JSON. No explanation.

Reference dates:
  Today    : {today} ({weekday})
  Tomorrow : {tomorrow}

{tags_section}

Fields:
  type          — "task" | "habit" | "note"
                  task  = a discrete, completable item with a clear done state
                          (e.g. "send Bob the report", "dentist appointment", "buy groceries")
                  habit = something you do repeatedly on an ongoing, indefinite basis with
                          no specific end (e.g. "exercise every morning", "meditate daily",
                          "journal every night", "drink 8 glasses of water each day")
                  note  = information to capture, not an action to perform. Use this when:
                            - input starts with "note:", "idea:", "thought:", "remember:",
                              "jot down", "write down", or similar capture phrases
                            - input describes a list ("shopping list", "list of X",
                              "grocery list", "packing list", "checklist for X")
                            - input is clearly informational (passwords, facts, recipes,
                              addresses, quotes, reference material)
                          Default to "task" when unclear.
  note_content  — For type "note" only: the full markdown content of the note.
                  For list intent, format items as a markdown checklist:
                    - [ ] item one
                    - [ ] item two
                  For other notes, write the content naturally in plain prose or markdown.
                  null for tasks and habits.
  title         — task, habit, or note name; preserve names, people, and key context from
                  the input; only strip date/time phrases; do NOT paraphrase or summarize
  description   — extra context only; null if none
  section       — "today" | "week" | "month" | "later"
                    later  = DEFAULT; use this whenever no date or deadline is mentioned
                    today  = explicitly due today
                    week   = due in 1-7 days (tomorrow, this week, this Friday, next few days)
                    month  = due in 8-30 days (in two weeks, in three weeks, next month)
                    IMPORTANT: if no deadline is stated, always use "later", never "week"
  scheduled_at  — ISO 8601 datetime string (YYYY-MM-DDTHH:MM:SS) only when the input
                  contains an explicit clock time (e.g. "at 3pm", "at noon", "morning",
                  "evening"). null if no time-of-day is mentioned at all.
                  If section is "later", scheduled_at MUST be null.
                  NEVER put natural language here — always resolve to a real date.
                  Common times: noon=12:00:00, midnight=00:00:00, morning=09:00:00,
                  afternoon=14:00:00, evening=18:00:00
  suggested_tags  — list of applicable tag names from the available tags; [] if none fit
  recurrence_rule — "daily" | "weekly" | "monthly" | "yearly" | null
                    Set only when the input explicitly describes a repeating task:
                    "every day" / "daily" / "each morning"  → "daily"   (section: "today")
                    "every week" / "weekly" / "every Monday" → "weekly"  (section: "week")
                    "every month" / "monthly"                → "monthly" (section: "month")
                    "every year" / "yearly" / "annually"     → "yearly"  (section: "later")
                    null if no recurrence is mentioned
                    When set, section must reflect the cadence above, not "later"\
"""


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

    def _build_examples(self, today: str, tomorrow: str) -> str:
        if not self.EXAMPLES:
            return ""
        lines = [f"Examples (today is {today}, tomorrow is {tomorrow}):"]
        for input_text, json_tmpl in self.EXAMPLES:
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

    _VALID_TYPES = {"task", "habit", "note"}

    def normalize_raw(self, raw: dict) -> dict:
        """
        Called on the raw dict from json.loads() before Pydantic validation.
        Use this to fix structural issues — wrong types, malformed values, etc.
        """
        type_val = str(raw.get("type", "task")).strip().lower()
        raw["type"] = type_val if type_val in self._VALID_TYPES else "task"

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
        for field in ("description", "note_content", "scheduled_at"):
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

    # Input prefixes that always signal a note regardless of LLM type output.
    _NOTE_PREFIXES = ("note:", "idea:", "thought:", "remember:", "jot down", "write down")

    def post_process(self, parsed: Any, *, text: str = "") -> Any:
        """
        Called on the validated ParsedTodo after Pydantic.
        `text` is the original user input — use it to check what was actually stated.
        """
        lowered = text.strip().lower()

        # Enforce section from explicit temporal phrases in the input text.
        # This catches cases where the LLM correctly understands the phrase but
        # maps it to the wrong section bucket.
        for pattern, forced_section in self._SECTION_OVERRIDES:
            if pattern.search(lowered):
                parsed.section = forced_section
                break

        # Enforce note type from capture-phrase prefixes stated in the prompt.
        if parsed.type != "note" and any(lowered.startswith(p) for p in self._NOTE_PREFIXES):
            parsed.type = "note"
            if parsed.note_content is None:
                # Strip the prefix and use the remainder as minimal note content
                for p in self._NOTE_PREFIXES:
                    if lowered.startswith(p):
                        parsed.note_content = text[len(p):].strip()
                        break

        # Recurring tasks default to "later" from the LLM because there's no
        # specific deadline — override with a section that matches the cadence.
        if parsed.recurrence_rule and parsed.section == "later":
            parsed.section = self._RECURRENCE_SECTION.get(parsed.recurrence_rule, "week")

        return parsed
