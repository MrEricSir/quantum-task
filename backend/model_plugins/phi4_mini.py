"""
Plugin for Microsoft phi4-mini via Ollama.

Known quirks this plugin corrects:
  - Infers scheduled_at=<today>T00:00:00 when no time is mentioned (midnight fix)
  - Sometimes assigns scheduled_at to "later" tasks (no-deadline fix)
  - Needs many concrete examples to follow section rules reliably
"""

from datetime import time as dt_time
from .base import BaseModelPlugin


class Phi4MiniPlugin(BaseModelPlugin):
    model_name = "phi4-mini"

    EXAMPLES = [
        (
            "call stacy tomorrow at noon",
            '{{"type":"task","title":"Call Stacy","description":null,"section":"week",'
            '"scheduled_at":"{tomorrow}T12:00:00","suggested_tags":[],"note_content":null}}',
        ),
        (
            "call john about plants",
            '{{"type":"task","title":"Call John about plants","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        (
            "buy groceries",
            '{{"type":"task","title":"Buy groceries","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        (
            "project deadline in three weeks",
            '{{"type":"task","title":"Project deadline","description":null,'
            '"section":"month","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        (
            "dentist appointment next week",
            '{{"type":"task","title":"Dentist appointment","description":null,'
            '"section":"week","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        # habit
        (
            "exercise every day",
            '{{"type":"habit","title":"Exercise","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":"daily","note_content":null}}',
        ),
        # note — capture phrase
        (
            "remember: Sarah\'s birthday is June 12",
            '{{"type":"note","title":"Sarah\'s birthday","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"note_content":"Sarah\'s birthday is June 12."}}',
        ),
        # note — list
        (
            "packing list: passport, charger, headphones",
            '{{"type":"note","title":"Packing list","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"note_content":"passport\\ncharger\\nheadphones"}}',
        ),
    ]

    def post_process(self, parsed, *, text: str = ""):
        # phi4-mini frequently returns <date>T00:00:00 when it infers a date
        # without an explicit time in the input.  Midnight is never a stated
        # time, so treat it as "no time given".
        if parsed.scheduled_at and parsed.scheduled_at.time() == dt_time(0, 0, 0):
            parsed.scheduled_at = None

        # "later" tasks have no deadline by definition.
        if parsed.section == "later":
            parsed.scheduled_at = None

        return parsed
