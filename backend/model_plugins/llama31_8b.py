"""
Plugin for Meta llama-3.1-8b-instruct (via Groq or Ollama).

Known quirks this plugin corrects:
  - Maps "tomorrow" to section="today" instead of "week"
  - Leaves time phrases (e.g. "9am") in the title instead of stripping them
  - Uses 12-hour clock in scheduled_at (e.g. "03:00" for 3pm) instead of 24-hour
  - Invents a scheduled_at for vague day phrases with no stated clock time
"""

import re

from .base import BaseModelPlugin

_TIME_RE = re.compile(
    r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b'
    r'|\b(?:noon|midnight|morning|afternoon|evening)\b',
    re.I,
)


class Llama31_8bPlugin(BaseModelPlugin):
    model_name = "llama-3.1-8b-instant"

    EXAMPLES = [
        # "tomorrow" → section=week; title has no time phrase; 24-hour scheduled_at
        (
            "call stacy tomorrow at noon",
            '{{"type":"task","title":"Call Stacy","description":null,"section":"week",'
            '"scheduled_at":"{tomorrow}T12:00:00","suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}}',
        ),
        # "today at 3pm" → 15:00 in 24-hour format; time stripped from title
        (
            "team meeting today at 3pm",
            '{{"type":"task","title":"Team meeting","description":null,"section":"today",'
            '"scheduled_at":"{today}T15:00:00","suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}}',
        ),
        # "today at 9am" → 09:00; time stripped from title
        (
            "standup today at 9am",
            '{{"type":"task","title":"Standup","description":null,"section":"today",'
            '"scheduled_at":"{today}T09:00:00","suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}}',
        ),
        # "tomorrow morning" → section=week, 09:00
        (
            "call the bank tomorrow morning",
            '{{"type":"task","title":"Call the bank","description":null,"section":"week",'
            '"scheduled_at":"{tomorrow}T09:00:00","suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}}',
        ),
        # no date → later; no scheduled_at
        (
            "call john about the project",
            '{{"type":"task","title":"Call John about the project","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}}',
        ),
        # "in N weeks" → month section
        (
            "project deadline in three weeks",
            '{{"type":"task","title":"Project deadline","description":null,'
            '"section":"month","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}}',
        ),
        # habit — daily recurring
        (
            "meditate every morning",
            '{{"type":"habit","title":"Meditate","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":"daily","note_content":null}}',
        ),
        # note
        (
            "note: wifi password is quantum42",
            '{{"type":"note","title":"WiFi password","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"note_content":"WiFi password is quantum42."}}',
        ),
    ]

    def post_process(self, parsed, *, text: str = ""):
        # Strip fabricated scheduled_at for vague day phrases with no stated clock time
        if parsed.scheduled_at and parsed.section in ("week", "month"):
            if not _TIME_RE.search(text):
                parsed.scheduled_at = None

        return super().post_process(parsed, text=text)
