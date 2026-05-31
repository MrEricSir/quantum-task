"""
Plugin for Meta llama3.2 (3B) via Ollama.

Known quirks this plugin corrects:
  - Occasionally maps "tomorrow ..." to section="today" instead of "week"
  - Misses section="today" when "today" precedes a time-of-day word ("today evening")
  - Under-suggests tags — needs an explicit tag example to stay calibrated
  - Under-classifies "in N weeks" — needs a month-section example
  - Invents an afternoon scheduled_at for "next week" events with no clock time stated
"""

from .base import BaseModelPlugin

# Words that indicate the user explicitly stated a time of day.
_TIME_WORDS = {"am", "pm", "noon", "midnight", "morning", "afternoon", "evening", "o'clock"}


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
        # note — explicit "note:" prefix
        (
            "note: the meeting room code is 4821",
            '{{"type":"note","title":"Meeting room code","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"note_content":"The meeting room code is 4821."}}',
        ),
        # note — list intent → markdown checklist
        (
            "grocery list: milk, eggs, bread",
            '{{"type":"note","title":"Grocery list","description":null,'
            '"section":"later","scheduled_at":null,"suggested_tags":[],'
            '"note_content":"- [ ] milk\\n- [ ] eggs\\n- [ ] bread"}}',
        ),
    ]

    def post_process(self, parsed, *, text: str = ""):
        # llama3.2 invents a scheduled_at for vague day phrases ("next week",
        # "this Friday") even when no clock time was stated.  If no time word
        # appears in the input, any scheduled_at on a week/month task is
        # fabricated — clear it.
        if parsed.scheduled_at and parsed.section in ("week", "month"):
            stated_words = set(text.lower().split())
            if not stated_words & _TIME_WORDS:
                parsed.scheduled_at = None

        return parsed
