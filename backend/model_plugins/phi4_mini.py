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
        # task — "tomorrow" → section=week
        (
            "call stacy tomorrow at noon",
            '{{"type":"task","title":"Call Stacy","description":null,"section":"week",'
            '"scheduled_at":"{tomorrow}T12:00:00","suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # task — no date → later; no scheduled_at
        (
            "call john about plants",
            '{{"type":"task","title":"Call John about plants","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # task — no date → later; no scheduled_at
        (
            "buy groceries",
            '{{"type":"task","title":"Buy groceries","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # task — "in N weeks" → month section
        (
            "project deadline in three weeks",
            '{{"type":"task","title":"Project deadline","description":null,'
            '"section":"month","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # task — "next week" → week section; no scheduled_at (no clock time)
        (
            "dentist appointment next week",
            '{{"type":"task","title":"Dentist appointment","description":null,'
            '"section":"week","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # habit — daily recurring
        (
            "exercise every day",
            '{{"type":"habit","title":"Exercise","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":"daily","clarification_question":null}}',
        ),
        # habit_check — past-tense habit completion
        (
            "did my meditation",
            '{{"type":"habit_check","title":"Meditation","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # food — eating/drinking log
        (
            "had a yogurt for breakfast",
            '{{"type":"food","title":"Yogurt","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # task_complete — marking a one-time task as done
        (
            "finished the dentist appointment",
            '{{"type":"task_complete","title":"Dentist appointment","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # assist — conversational/planning request
        (
            "help me plan my week",
            '{{"type":"assist","title":"Help me plan my week","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
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

        return super().post_process(parsed, text=text)
