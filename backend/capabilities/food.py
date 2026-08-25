# Shared description of food/drink logging for LLM prompts, AND the shared
# implementation for turning raw food-log text into structured entries.
# Imported by model_plugins/base.py (parse flow) and telegram/bot.py (intent
# flow) for the prompt strings; parse_food_entries() below is imported by
# routers/food.py (webapp) and telegram/bot.py (Telegram) so both surfaces
# split multi-item messages and enrich each item identically -- there is
# deliberately only one place this logic lives.

import json

# Embedded in BASE_INSTRUCTIONS under the `type` field breakdown.
# First line has no leading indent (the caller supplies the 18-space prefix).
# Continuation lines carry 26-space indent to align with the rest of the block.
PARSE_DESCRIPTION = """\
food  = logging something eaten or drunk (past, present, or imminent)
                          Trigger on ANY first- or second-person eating/drinking verb:
                          ate, eat, eating, had, have, having, drank, drink, drinking,
                          consumed, grabbed, picked up, ordered, finished, just had, etc.
                          Examples: "I ate a donut", "had a cup of yogurt",
                          "ate sugar-free yogurt", "just had coffee", "drinking a beer",
                          "had a chicken salad for lunch", "grabbed a snack"
                          Do NOT classify food as a task or goal just because it mentions
                          a health attribute (e.g. "sugar-free", "low-cal", "protein shake").
                          When type is "food", set title to the food/drink description only\
"""

# Embedded in _TELEGRAM_INTENT_PROMPT as a top-level action block.
TELEGRAM_DESCRIPTION = """\
  "log_food"
      User is logging something they ate or drank. Use this whenever food or drink
      is mentioned in a past-tense or "I had / I ate / I drank" context.
      Also return:
        "raw_input" — exact food/drink description from the user's message
      Examples: "I had yogurt and iced tea for breakfast", "just ate a salad for lunch",
                "had a coffee", "ate a muffin", "grabbed a green smoothie"\
"""


# ── Shared parse + split implementation ─────────────────────────────────────

_PARSE_SYSTEM = """\
You parse food and drink log entries into structured data. \
Respond with ONLY valid JSON (no markdown, no explanation).

Split the input into one item per distinct food or drink mentioned -- e.g. "had a bagel,
coffee, and a banana" is THREE separate items ("Bagel", "Coffee", "Banana"), never one
item with a combined name. Only combine into one item when it's genuinely a single dish
someone would describe as one thing (e.g. "chicken salad", "coffee with oat milk").

{{
  "items": [
    {{
      "name":        "concise name of what was consumed, e.g. 'donut', 'coffee with oat milk', 'chicken salad'",
      "category":    "food" | "drink",
      "source_text": "verbatim fragment of the input this item was parsed from",
      "notes":       "1-2 sentence honest nutritional assessment — be specific, not preachy",
      "quality":     integer 1-10 (10 = highly nutritious whole foods; 1 = pure junk with no redeeming value),
      "calories":    integer estimated kcal — best estimate for a typical serving; null only if truly impossible
    }}
  ]
}}

assumption rule:
- Unless milk, cream, sugar, syrup, honey, or another addition is explicitly
  mentioned, assume the plain/black/unsweetened form (e.g. "tea" and "coffee"
  mean plain tea and black coffee, not a version with added milk or sugar)

quality examples:
- leafy salad, grilled salmon → 9
- black coffee, plain tea (no milk/sugar) → 8
- oatmeal with fruit → 8
- banana → 7
- coffee with milk → 6
- white rice with vegetables → 6
- pizza slice → 4
- donut → 3
- bag of chips → 3
- energy drink → 2

calories examples:
- black coffee → 5
- plain tea → 2
- coffee with oat milk → 60
- banana → 90
- donut → 300
- pizza slice → 285
- chicken salad (large) → 450
- bag of chips (regular) → 150
"""

_FALLBACK_ENTRY_TEMPLATE = {
    "category": "food", "source_text": None, "notes": None, "quality": None, "calories": None,
}


def _normalize_item(item: dict, raw: str) -> dict:
    name = str(item.get("name") or raw[:80])
    category = item.get("category")
    if category not in ("food", "drink"):
        category = "food"
    source_text = item.get("source_text") or None
    notes = item.get("notes") or None
    quality = item.get("quality")
    if quality is not None:
        try:
            quality = max(1, min(10, int(quality)))
        except (TypeError, ValueError):
            quality = None
    calories = item.get("calories")
    if calories is not None:
        try:
            calories = max(0, int(calories))
        except (TypeError, ValueError):
            calories = None
    return {
        "name": name, "category": category, "source_text": source_text,
        "notes": notes, "quality": quality, "calories": calories,
    }


def parse_food_entries(raw: str) -> list[dict]:
    """Split raw food-log text into one structured dict per distinct item:
    name, category, source_text (the verbatim fragment for that item), notes,
    quality, calories. The single source of truth for turning free text into
    food entries -- both the webapp's food log (routers/food.py) and
    Telegram's log_food handler (telegram/bot.py) call this, so a message
    logging multiple items splits and gets enriched the same way regardless
    of which surface it came from.
    """
    from deps import llm_client, LLM_MODEL
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _PARSE_SYSTEM},
                {"role": "user",   "content": raw},
            ],
            max_tokens=600,
            response_format={"type": "json_object"},
            # See correlations.py's _generate_experiment for the full story: on a reasoning
            # model, an unbounded chain-of-thought can burn the whole max_tokens budget
            # before the real JSON answer, truncating it.
            reasoning_effort="low",
        )
        data = json.loads(resp.choices[0].message.content.strip())
        raw_items = data.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            # Tolerate a model that returns a single object instead of {"items": [...]}
            raw_items = [data]
        return [_normalize_item(item, raw) for item in raw_items]
    except Exception as e:
        # Falls back to the verbatim raw text as the entry name, with no fabricated
        # category/quality/calories -- an honest degradation, not fake content. Logged so a
        # real, recurring failure is visible instead of just invisibly losing all enrichment.
        print(f"[capabilities.food] parse error: {e}")
        return [{"name": raw[:120], **_FALLBACK_ENTRY_TEMPLATE}]
