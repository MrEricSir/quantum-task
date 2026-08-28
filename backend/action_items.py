"""
Action-item extraction from a card's pasted body text (meeting notes, an
email, planning notes, etc.) into structured ParsedCard tasks.

Deliberately a single direct LLM call rather than routed through
model_plugins' per-model prompt system -- extraction is one well-defined
task, not the kind of classification quirk (weak local models needing
different prompt engineering per model) model_plugins exists to paper over.
See capabilities/food.py for the same reasoning applied to food-log parsing.

Reuses schemas.ParsedCard / model_plugins.base.resolve_dates so extracted
items flow through the exact same shape -- and the same QuickAddModal
bulk-confirm review screen -- as Quick Add's own parsed items.
"""
import json
from datetime import date

from sqlalchemy.orm import Session

import schemas
from deps import llm_client, LLM_MODEL, reasoning_kwargs
from model_plugins.base import resolve_dates
from tag_prompts import tags_prompt_section

_EXTRACT_SYSTEM = """\
You extract concrete action items from a pasted document (meeting notes, an email, \
planning notes, etc.) into a JSON list of tasks.

An action item is a personal commitment or next step someone needs to DO, separate from \
the document itself -- something you'd add to a to-do list. Do NOT extract: topics that \
were merely discussed, decisions already made with no further action required, background \
context, vague aspirations, or steps that are themselves the document's own content (e.g. \
numbered steps in a recipe or how-to, or instructions someone is already following as \
they read -- "preheat the oven", "bake for 12 minutes" is what the document IS, not a \
commitment extracted FROM it). If the document contains NO real action items, return an \
empty list -- never invent one just to have output.

If a name is attached to an action (e.g. "Sarah will follow up with design"), include \
the name in the title.

CRITICAL: the literal word "action" appearing in the text (e.g. "no action needed", \
"action items: none") is never itself an action item -- read what it actually says, \
don't pattern-match on the word.

CRITICAL: a single message often contains MULTIPLE distinct asks. Watch for enumeration \
("1)", "2)", "first", "second", "also", "additionally", separate sentences each asking \
for something different) and extract EVERY one as its own separate item -- do not stop \
after the first one, and do not merge distinct asks into a single item.

Examples:
  Input : "Quick update: the migration finished successfully last night. No action \
needed on our end."
  Output: {{"items": []}}  -- this is a status report, not a request for anyone to do \
anything.
  Input : "Grandma's chocolate chip cookies: 2 cups flour, 1 cup butter. Bake at 350F \
for 12 minutes."
  Output: {{"items": []}}  -- a recipe's own steps are not action items extracted from it.
  Input : "Sarah will follow up with design about onboarding by Friday. John will draft \
new pricing copy next week."
  Output: {{"items": [{{"title": "Follow up with design about onboarding", "source_text": \
"Sarah will follow up with design about onboarding by Friday", ...}}, {{"title": "Draft \
new pricing copy", "source_text": "John will draft new pricing copy next week", ...}}]}}
  Input : "Two things before Thursday: 1) please send the updated budget numbers, and \
2) can someone confirm the venue for the conference?"
  Output: {{"items": [{{"title": "Send updated budget numbers", "source_text": "two \
things before Thursday: 1) please send the updated budget numbers", ...}}, {{"title": \
"Confirm the conference venue", "source_text": "2) can someone confirm the venue for the \
conference", ...}}]}}  -- two separate numbered asks, both extracted, neither dropped, \
each source_text keeps enough of the surrounding sentence to carry its own date phrase.

Current date: {today} ({weekday}). Only set "scheduled_at" when the text names an \
explicit date or deadline (e.g. "by Friday", "next week") -- leave it null otherwise. \
Use ISO 8601 (e.g. "2026-06-05T00:00:00") when set.

{tags_section}
Only suggest a tag when it's a clear match -- an empty list is normal and expected.

Return ONLY valid JSON, no prose, no markdown:
{{"items": [{{"title": "short, actionable, starts with a verb when possible", \
"description": "brief extra context, or null -- not the full original sentence", \
"scheduled_at": "ISO datetime, or null", \
"source_text": "the verbatim fragment of the input this item was parsed from, \
including any date/deadline phrase -- copy the exact words, do not paraphrase", \
"suggested_tags": ["zero or more names from the available tags list"]}}]}}\
"""


def extract_action_items(db: Session, text: str, today: date) -> list[schemas.ParsedCard]:
    """Scan `text` for action items and return them as ParsedCard tasks,
    date-resolved the same way Quick Add resolves relative dates. Raises on
    LLM failure -- callers should catch and translate to an HTTP error, same
    as routers.cards.parse_bulk_text's own callers do.
    """
    tags_section = tags_prompt_section(db)
    prompt = _EXTRACT_SYSTEM.format(
        today=today.isoformat(), weekday=today.strftime("%A"), tags_section=tags_section,
    )

    client = llm_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        **reasoning_kwargs(),
    )
    data = json.loads(response.choices[0].message.content)
    raw_items = data.get("items", [])

    items = []
    for raw in raw_items:
        try:
            parsed = schemas.ParsedCard.model_validate(raw)
        except Exception:
            continue  # skip a single malformed item rather than fail the whole extraction
        # source_text (the verbatim input fragment) carries any date phrase the LLM
        # stripped out of the title (e.g. "by Friday") -- resolve_dates needs to see
        # that phrase to deterministically correct weekday arithmetic; the sanitized
        # title alone usually doesn't contain it. Falls back to title only if the LLM
        # omitted source_text.
        parsed = resolve_dates(parsed, text=parsed.source_text or parsed.title, today=today)
        items.append(parsed)
    return items
