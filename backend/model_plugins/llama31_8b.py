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
    r'|\b(?:noon|midnight|morning|afternoon|evening)\b'
    r'|\bat\s+\d{1,2}(?::\d{2})?\b',  # bare "at N" with no am/pm
    re.I,
)

# Past-tense / present-progressive eating/drinking verbs — override task→food
_FOOD_RE = re.compile(
    r'^(?:i\s+)?(?:ate|eating|had|having|drank|drinking|'
    r'consumed|grabbed|ordered|just\s+had|snacked\s+on|tasted)\b',
    re.I,
)

# Past-tense completion verbs — override task/habit → habit_check
# Frontend handles habit vs task disambiguation via unified picker
_HABIT_CHECK_RE = re.compile(
    r'^(?:i\s+)?(?:did|complete(?:d)?|finish(?:ed)?|check(?:ed)?\s+off|done\s+with)\b',
    re.I,
)

_HABIT_CHECK_STRIP_RE = re.compile(
    r'^(?:i\s+)?(?:did|complete(?:d)?|finish(?:ed)?|check(?:ed)?\s+off|done\s+with)'
    r'(?:\s+(?:my|the|a|an))?\s+',
    re.I,
)

# Natural past tense of habitually recurring activities — used as a fallback
# when the model misclassifies as "task". Only changes type, not title.
_HABITUAL_PAST_RE = re.compile(
    r'^(?:i\s+)?(?:talked?|spoke)\s+(?:to|with)\s+(?:a\b|an\b|some\b|someone\b|a\s+\w+)\b'
    r'|^(?:i\s+)?(?:meditat|journal|stretch|exercis|practic|walked|jogged|cycled|biked?|swam|hiked?|ran\b)\w*\b'
    r'|^(?:i\s+)?went\s+for\s+(?:a|my)\s+\w+\b',
    re.I,
)

# "archive" is the only explicit task_complete signal at the regex level
_TASK_COMPLETE_RE = re.compile(r'^(?:i\s+)?archived?\b', re.I)

_TASK_COMPLETE_STRIP_RE = re.compile(
    r'^(?:i\s+)?archived?(?:\s+(?:the|a|an))?\s+', re.I
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
        # habit_check — explicit completion verb
        (
            "did my meditation",
            '{{"type":"habit_check","title":"Meditation","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"clarification_question":null}}',
        ),
        (
            "completed my morning run",
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
            "had a yogurt",
            '{{"type":"food","title":"Yogurt","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}}',
        ),
        (
            "ate a bowl of oatmeal for breakfast",
            '{{"type":"food","title":"Bowl of oatmeal","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}}',
        ),
        # task_complete — marking a one-time task as done
        (
            "finished the dentist appointment",
            '{{\"type\":\"task_complete\",\"title\":\"Dentist appointment\",\"description\":null,'\
            '\"section\":\"today\",\"scheduled_at\":null,\"suggested_tags\":[],'\
            '\"recurrence_rule\":null,\"note_content\":null}}',
        ),
        (
            "archive the project proposal",
            '{{\"type\":\"task_complete\",\"title\":\"Project proposal\",\"description\":null,'\
            '\"section\":\"later\",\"scheduled_at\":null,\"suggested_tags\":[],'\
            '\"recurrence_rule\":null,\"note_content\":null}}',
        ),
        # assist — conversational/planning request, not a structured item
        (
            "help me plan my week",
            '{{\"type\":\"assist\",\"title\":\"Help me plan my week\",\"description\":null,'
            '\"section\":\"later\",\"scheduled_at\":null,\"suggested_tags\":[],'
            '\"recurrence_rule\":null}}',
        ),
        (
            "what should I focus on today?",
            '{{\"type\":\"assist\",\"title\":\"What should I focus on today?\",\"description\":null,'
            '\"section\":\"later\",\"scheduled_at\":null,\"suggested_tags\":[],'
            '\"recurrence_rule\":null}}',
        ),
        # reference capture — "note:" prefix → task/later with description
        (
            "note: wifi password is quantum42",
            '{{"type":"task","title":"WiFi password","description":"WiFi password is quantum42.",'
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
            '"scheduled_at":"{today}T15:00:00","suggested_tags":[],"recurrence_rule":null}},'
            '{{"type":"task","title":"Shopping list","description":"lettuce\\nmilk\\nbagels",'
            '"section":"later","scheduled_at":null,"suggested_tags":[],"recurrence_rule":null}},'
            '{{"type":"habit","title":"Eat fiber","description":null,"section":"today",'
            '"scheduled_at":null,"suggested_tags":[],"recurrence_rule":"daily"}}'
            ']}}',
        ),
        # Multi-task comma-separated input → separate items, times resolved
        (
            "call tom at 6pm, dinner with andre at 7",
            '{{"items":['
            '{{"type":"task","title":"Call Tom","description":null,"section":"today",'
            '"scheduled_at":"{today}T18:00:00","suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}},'
            '{{"type":"task","title":"Dinner with Andre","description":null,"section":"today",'
            '"scheduled_at":"{today}T19:00:00","suggested_tags":[],'
            '"recurrence_rule":null,"note_content":null}}'
            ']}}',
        ),
        # "buy X, Y, and Z" with multiple items → single task with description
        (
            "buy eggs, fish, and apple juice",
            '{{"items":[{{"type":"task","title":"Shopping list","description":"eggs\\nfish\\napple juice",'
            '"section":"later","scheduled_at":null,"suggested_tags":[],"recurrence_rule":null}}]}}',
        ),
        # Store name + items on separate lines → single task with description
        (
            "trader joe\'s\nmuffin mix\nyogurt\nsalad",
            '{{"items":[{{"type":"task","title":"Trader Joe\'s shopping list",'
            '"description":"Trader Joe\'s\\nmuffin mix\\nyogurt\\nsalad",'
            '"section":"later","scheduled_at":null,"suggested_tags":[],"recurrence_rule":null}}]}}',
        ),
    ]

    def post_process(self, parsed, *, text: str = ""):
        if parsed.type == "assist":
            return parsed

        # Override task/habit → habit_check for past-tense completion verbs
        # Frontend resolves whether it's a habit or task via unified picker
        if parsed.type in ("task", "habit") and _HABIT_CHECK_RE.match(text.strip()):
            parsed.type = "habit_check"
            stripped = _HABIT_CHECK_STRIP_RE.sub("", text.strip()).strip()
            if stripped:
                parsed.title = stripped[0].upper() + stripped[1:]

        # Natural past-tense habitual activity — type override only, preserve title.
        if parsed.type == "task" and _HABITUAL_PAST_RE.match(text.strip()):
            parsed.type = "habit_check"

        # Override task→food when input clearly starts with an eating/drinking verb
        if parsed.type == "task" and _FOOD_RE.match(text.strip()):
            parsed.type = "food"

        # "archive" is the only regex-level trigger for task_complete
        if parsed.type == "task" and _TASK_COMPLETE_RE.match(text.strip()):
            parsed.type = "task_complete"
            stripped = _TASK_COMPLETE_STRIP_RE.sub("", text.strip()).strip()
            if stripped:
                parsed.title = stripped[0].upper() + stripped[1:]

        # Strip fabricated scheduled_at for vague day phrases with no stated clock time
        if parsed.scheduled_at and parsed.section in ("week", "month"):
            if not _TIME_RE.search(text):
                parsed.scheduled_at = None

        return super().post_process(parsed, text=text)
