"""
Plugin for Meta llama-3.3-70b-versatile via Groq.

llama-3.3-70b follows the system prompt well and correctly outputs all
types (food, habit_check, task_complete, assist) without regex overrides.
The one known quirk shared across the llama family:
  - Fabricates a scheduled_at for vague day phrases ("next week", "this
    Friday") even when no clock time is stated — cleared here.
"""

import re

from .base import BaseModelPlugin

_TIME_RE = re.compile(
    r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b'
    r'|\b(?:noon|midnight|morning|afternoon|evening)\b'
    r'|\bat\s+\d{1,2}(?::\d{2})?\b',
    re.I,
)


class Llama33_70bPlugin(BaseModelPlugin):
    model_name = "llama-3.3-70b-versatile"

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
            "call john about the project",
            '{{"type":"task","title":"Call John about the project","description":null,'
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
        # habit — daily recurring
        (
            "meditate every morning",
            '{{"type":"habit","title":"Meditate","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":"daily","clarification_question":null}}',
        ),
        # habit_check — explicit completion verb
        (
            "did my meditation",
            '{{"type":"habit_check","title":"Meditation","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        (
            "finished my morning run",
            '{{"type":"habit_check","title":"Morning run","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # habit_check — natural past tense WITHOUT completion verb
        (
            "talked to a stranger",
            '{{"type":"habit_check","title":"Talk to a stranger","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        (
            "went for a run this morning",
            '{{"type":"habit_check","title":"Run","description":null,'
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
        (
            "ate a banana and drank some coffee",
            '{{"type":"food","title":"Banana and coffee","description":null,'
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
        (
            "archive the project proposal",
            '{{"type":"task_complete","title":"Project proposal","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        # assist — conversational/planning request
        (
            "help me plan my week",
            '{{"type":"assist","title":"Help me plan my week","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        (
            "what should I focus on today?",
            '{{"type":"assist","title":"What should I focus on today?","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
    ]

    BULK_EXAMPLES = [
        # Mixed: task with time + shopping list + habit
        (
            "call sam at 3pm, buy lettuce milk bagels, add a habit to eat fiber",
            '{{"items":['
            '{{"type":"task","title":"Call Sam","description":null,"section":"today",'
            '"scheduled_at":"{today}T15:00:00","suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}},'
            '{{"type":"task","title":"Buy lettuce, milk, bagels","description":"lettuce\\nmilk\\nbagels",'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}},'
            '{{"type":"habit","title":"Eat fiber","description":null,"section":"today",'
            '"scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":"daily","clarification_question":null}}'
            ']}}',
        ),
        # food + task together → separate items
        (
            "had oatmeal for breakfast, call dentist tomorrow",
            '{{"items":['
            '{{"type":"food","title":"Oatmeal","description":null,"section":"today",'
            '"scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}},'
            '{{"type":"task","title":"Call dentist","description":null,"section":"week",'
            '"scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}'
            ']}}',
        ),
    ]

    def post_process(self, parsed, *, text: str = ""):
        if parsed.type == "assist":
            return parsed

        # llama-family models fabricate a scheduled_at for vague day phrases
        # ("next week", "this Friday") when no clock time is stated — clear it.
        if parsed.scheduled_at and parsed.section in ("week", "month"):
            if not _TIME_RE.search(text):
                parsed.scheduled_at = None

        return super().post_process(parsed, text=text)
