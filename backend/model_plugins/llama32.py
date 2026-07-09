"""
Plugin for Meta llama3.2 (3B) via Ollama.

Known quirks this plugin corrects:
  - Occasionally maps "tomorrow ..." to section="today" instead of "week"
  - Misses section="today" when "today" precedes a time-of-day word ("today evening")
  - Under-suggests tags — needs an explicit tag example to stay calibrated
  - Under-classifies "in N weeks" — needs a month-section example
  - Invents an afternoon scheduled_at for "next week" events with no clock time stated
"""

import re

from .base import BaseModelPlugin

_TIME_RE = re.compile(
    r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b'
    r'|\b(?:noon|midnight|morning|afternoon|evening)\b'
    r'|\bat\s+\d{1,2}(?::\d{2})?\b',  # bare "at N" with no am/pm
    re.I,
)

# First-person eating/drinking verbs that llama3.2 under-classifies as "task".
# Only clear past tense / present progressive — avoids false positives on
# habits like "drink more water daily" or imperatives like "eat less sugar".
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

# "archive" is the only explicit task_complete signal at the regex level
_TASK_COMPLETE_RE = re.compile(r'^(?:i\s+)?archived?\b', re.I)

_TASK_COMPLETE_STRIP_RE = re.compile(
    r'^(?:i\s+)?archived?(?:\s+(?:the|a|an))?\s+', re.I
)


class Llama32Plugin(BaseModelPlugin):
    model_name = "llama3.2"

    EXAMPLES = [
        # task — "tomorrow" must map to section=week
        (
            "call stacy tomorrow at noon",
            '{{"type":"task","title":"Call Stacy","description":null,"section":"week",'
            '"scheduled_at":"{tomorrow}T12:00:00","suggested_tags":[],"note_content":null}}',
        ),
        # task — "today + time-of-day word" must stay section=today
        (
            "cook dinner today evening",
            '{{"type":"task","title":"Cook dinner","description":null,"section":"today",'
            '"scheduled_at":"{today}T18:00:00","suggested_tags":[],"note_content":null}}',
        ),
        # task — no date → later
        (
            "call john about plants",
            '{{"type":"task","title":"Call John about plants","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        # task — "in N weeks" → month section
        (
            "project deadline in three weeks",
            '{{"type":"task","title":"Project deadline","description":null,'
            '"section":"month","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        # habit — daily recurring behavior
        (
            "meditate every morning",
            '{{"type":"habit","title":"Meditate","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],'
            '"recurrence_rule":"daily","note_content":null}}',
        ),
        # habit_check — past-tense habit completion
        (
            "did my meditation",
            '{{"type":"habit_check","title":"Meditation","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        (
            "finished my evening walk",
            '{{"type":"habit_check","title":"Evening walk","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        # food — eating/drinking log (past or present tense)
        (
            "had a cup of sugar-free yogurt",
            '{{"type":"food","title":"Cup of sugar-free yogurt","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        (
            "ate a banana and drank some coffee",
            '{{"type":"food","title":"Banana and coffee","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        # task_complete — marking a one-time task as done
        (
            "finished the dentist appointment",
            '{{"type":"task_complete","title":"Dentist appointment","description":null,'
            '"section":"today","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        (
            "archive the project proposal",
            '{{"type":"task_complete","title":"Project proposal","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],"note_content":null}}',
        ),
        # note — explicit "note:" prefix
        (
            "note: the meeting room code is 4821",
            '{{"type":"note","title":"Meeting room code","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"note_content":"The meeting room code is 4821."}}',
        ),
        (
            "grocery list: milk, eggs, bread",
            '{{"type":"note","title":"Grocery list","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"note_content":"milk\\neggs\\nbread"}}',
        ),
    ]

    BULK_EXAMPLES = [
        # Mixed: task with time + shopping list → note + habit
        (
            "call sam at 3pm, buy lettuce milk bagels, add a habit to eat fiber",
            '{{"items":['
            '{{"type":"task","title":"Call Sam","description":null,"section":"today",'
            '"scheduled_at":"{today}T15:00:00","suggested_tags":[],"note_content":null}},'
            '{{"type":"note","title":"Shopping list","description":null,"section":"later",'
            '"scheduled_at":null,"suggested_tags":[],"note_content":"lettuce\\nmilk\\nbagels"}},'
            '{{"type":"habit","title":"Eat fiber","description":null,"section":"today",'
            '"scheduled_at":null,"suggested_tags":[],"recurrence_rule":"daily","note_content":null}}'
            ']}}',
        ),
        # Multi-task comma-separated input → separate items, times resolved
        (
            "call tom at 6pm, dinner with andre at 7",
            '{{"items":['
            '{{"type":"task","title":"Call Tom","description":null,"section":"today",'
            '"scheduled_at":"{today}T18:00:00","suggested_tags":[],"note_content":null}},'
            '{{"type":"task","title":"Dinner with Andre","description":null,"section":"today",'
            '"scheduled_at":"{today}T19:00:00","suggested_tags":[],"note_content":null}}'
            ']}}',
        ),
        # food + task together → separate items
        (
            "had oatmeal for breakfast, call dentist tomorrow",
            '{{"items":['
            '{{"type":"food","title":"Oatmeal","description":null,"section":"today",'
            '"scheduled_at":null,"suggested_tags":[],"note_content":null}},'
            '{{"type":"task","title":"Call dentist","description":null,"section":"week",'
            '"scheduled_at":null,"suggested_tags":[],"note_content":null}}'
            ']}}',
        ),
        # "buy X, Y, and Z" with multiple items → single note
        (
            "buy eggs, fish, and apple juice",
            '{{"items":[{{"type":"note","title":"Shopping list","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"note_content":"eggs\\nfish\\napple juice"}}]}}',
        ),
        # Store name + items on separate lines → single note
        (
            "trader joe\'s\nmuffin mix\nyogurt\nsalad",
            '{{"items":[{{"type":"note","title":"Trader Joe\'s shopping list","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"note_content":"Trader Joe\'s\\nmuffin mix\\nyogurt\\nsalad"}}]}}',
        ),
    ]

    def post_process(self, parsed, *, text: str = ""):
        # Override task/habit → habit_check for past-tense completion verbs
        # Frontend resolves whether it's a habit or task via unified picker
        if parsed.type in ("task", "habit") and _HABIT_CHECK_RE.match(text.strip()):
            parsed.type = "habit_check"
            stripped = _HABIT_CHECK_STRIP_RE.sub("", text.strip()).strip()
            if stripped:
                parsed.title = stripped[0].upper() + stripped[1:]

        # Override task→food for eating/drinking verbs
        if parsed.type == "task" and _FOOD_RE.match(text.strip()):
            parsed.type = "food"

        # "archive" is the only regex-level trigger for task_complete
        if parsed.type == "task" and _TASK_COMPLETE_RE.match(text.strip()):
            parsed.type = "task_complete"
            stripped = _TASK_COMPLETE_STRIP_RE.sub("", text.strip()).strip()
            if stripped:
                parsed.title = stripped[0].upper() + stripped[1:]

        # llama3.2 invents a scheduled_at for vague day phrases ("next week",
        # "this Friday") even when no clock time was stated.  If no time word
        # appears in the input, any scheduled_at on a week/month task is
        # fabricated — clear it.
        if parsed.scheduled_at and parsed.section in ("week", "month"):
            if not _TIME_RE.search(text):
                parsed.scheduled_at = None

        return super().post_process(parsed, text=text)
