"""
Context-gathering helpers for the AI assistant.

These functions turn raw signals (coordinates, a user message, DB rows) into
prompt-ready strings or extra context for the LLM calls in assist.generate.
"""
import json
import os
from datetime import timedelta

import requests as http_requests
from sqlalchemy.orm import selectinload

import models
from deps import llm_client, LLM_MODEL
from gcal import get_personal_events

_ASSIST_TAVILY_KEY = os.getenv("TAVILY_API_KEY", "")

# ── Reverse-geocode cache ──────────────────────────────────────────────────────

_geocode_cache: dict = {}


def _reverse_geocode(lat: float, lon: float) -> str | None:
    key = (round(lat, 2), round(lon, 2))
    if key in _geocode_cache:
        return _geocode_cache[key]
    try:
        r = http_requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "quantum-task/1.0"},
            timeout=5,
        )
        addr = r.json().get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county")
        state = addr.get("state")
        country = addr.get("country_code", "").upper()
        parts = [p for p in [city, state] if p]
        if country and country != "US":
            parts.append(country)
        result = ", ".join(parts) if parts else None
    except Exception:
        result = None
    _geocode_cache[key] = result
    return result


# ── Web search ──────────────────────────────────────────────────────────────────

def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    if not _ASSIST_TAVILY_KEY:
        return []
    try:
        r = http_requests.post(
            "https://api.tavily.com/search",
            json={"api_key": _ASSIST_TAVILY_KEY, "query": query, "max_results": max_results},
            timeout=15,
        )
        r.raise_for_status()
        return [
            {"title": res.get("title", ""), "url": res.get("url", ""), "content": res.get("content", "")}
            for res in r.json().get("results", [])
        ]
    except Exception:
        return []


_ASSIST_DECISION_SYSTEM = """\
Decide whether answering this request requires current web data \
(local businesses, real-time info, current events, prices, reviews, hours, etc.).
Return ONLY valid JSON — no markdown.
If search needed: {"search": true, "queries": ["specific query 1", "specific query 2"]}
If not needed: {"search": false}
Use 1–3 targeted queries. Only include a location in queries if the user explicitly \
provided one — never assume or infer a location.
IMPORTANT: If the request involves finding specific places or businesses (hotels, \
restaurants, stores) but no location is given, return {"search": false} — the \
assistant will ask the user for their location instead of guessing.
"""


def _maybe_web_search(user_msg: str) -> str:
    """Run the web-search decision + search. Returns extra context string (may be empty)."""
    if not _ASSIST_TAVILY_KEY:
        return ""
    try:
        decision_resp = llm_client().chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _ASSIST_DECISION_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0,
            timeout=10,
            # See correlations.py's _generate_experiment for the full story: on a reasoning
            # model, an unbounded chain-of-thought (a separate field, not mixed into content)
            # can burn the whole max_tokens budget before the real JSON answer, truncating it.
            # "low" is plenty for a one-line true/false decision like this.
            reasoning_effort="low",
            response_format={"type": "json_object"},
        )
        decision = json.loads(decision_resp.choices[0].message.content.strip())
        if not decision.get("search"):
            return ""
        all_results = []
        for q in decision.get("queries", [])[:3]:
            all_results.extend(_tavily_search(q))
        if all_results:
            parts = [f"[{r['title']}]({r['url']})\n{r['content']}" for r in all_results[:8]]
            return "\n\n---\n\n".join(parts)
    except Exception as e:
        # Deliberately swallowed, not surfaced to the user -- this is a best-effort add-on to
        # an otherwise-still-useful assist response, and the caller only shows a "searching"
        # indicator once real results come back (never claims to search and silently fails to),
        # so there's nothing false being displayed. Logged so a real, recurring failure here
        # (as opposed to an occasional network hiccup) is at least visible in the logs instead
        # of just invisibly never happening.
        print(f"[assist] web search decision error: {e}")
    return ""


# ── Calendar / GitHub context ────────────────────────────────────────────────

def _calendar_context_lines(db, today, tz_offset: int) -> list[str]:
    """Return formatted calendar event lines for the next 7 days, or an empty list."""
    if db.query(models.CalendarMapping).count() == 0:
        return []
    try:
        day_labels = {today: "Today", today + timedelta(days=1): "Tomorrow"}
        cal_by_day: dict[str, list[str]] = {}
        events = get_personal_events(db, today, today + timedelta(days=8), tz_offset)
        for ev in events:
            ev_date = ev["local_date"]
            day_key = ev_date.isoformat()
            label   = day_labels.get(ev_date) or ev_date.strftime("%A, %b %-d")
            if day_key not in cal_by_day:
                cal_by_day[day_key] = [label]
            cal_by_day[day_key].append(f"  {ev['time_str']} — {ev['title']}")
        if not cal_by_day:
            return ["### Upcoming calendar events", "  No events scheduled in the next 7 days."]
        lines = ["### Upcoming calendar events"]
        for day_key in sorted(cal_by_day):
            lines.extend(cal_by_day[day_key])
        return lines
    except Exception as e:
        print(f"[assist/calendar] fetch error: {e}")
        return ["### Upcoming calendar events", "  Calendar temporarily unavailable."]


def _github_context_lines(db, card) -> list[str]:
    """Return formatted GitHub issue/PR context lines for a card linked via external_id."""
    if not card.external_id:
        return []
    try:
        eng_item = (
            db.query(models.EngineeringItem)
            .options(selectinload(models.EngineeringItem.comments))
            .filter_by(external_id=card.external_id)
            .first()
        )
        if not eng_item:
            return []

        kind = "PR" if eng_item.item_type == "pr" else "Issue"
        lines = [
            f"### GitHub {kind}: {eng_item.repo}#{eng_item.number}",
            f"Title: {eng_item.title}",
            f"Status: {eng_item.state}",
            f"URL: {eng_item.url}",
        ]

        if eng_item.body and eng_item.body.strip():
            lines.append("\n**Description:**")
            lines.append(eng_item.body.strip())

        if eng_item.comments:
            lines.append("\n**Comments:**")
            for c in eng_item.comments:
                date_str = c.created_at.strftime("%Y-%m-%d") if c.created_at else ""
                author = c.author or "unknown"
                lines.append(f"\n[{author}] ({date_str}):")
                lines.append(c.body.strip() if c.body else "")

        return lines
    except Exception as e:
        print(f"[assist/github] context error: {e}")
        return []
