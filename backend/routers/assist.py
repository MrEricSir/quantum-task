"""
Assistant endpoints.

- /api/cards/{id}/thread  — persistent multi-turn conversation attached to a card
- /api/assist/stream      — one-shot card assist (legacy, still used by QuickAddModal)
- /api/assist/global      — header-level assistant with section/tag context
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.requests import Request

import models
import schemas
from database import SessionLocal
from deps import get_db, llm_client, LLM_MODEL, local_date, utc_offset_minutes
from gcal import get_personal_events
from health_context import build_health_context

router = APIRouter()

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


# ── Shared LLM helpers ─────────────────────────────────────────────────────────

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

_ASSIST_SYSTEM = """\
You are a personal administrative assistant. You produce content the user can act on — \
you do not perform actions yourself.

What "producing content" means:
- Request is a question about schedule, tasks, or data → answer it directly from the \
context provided; do not invent information
- Request is a reply to send → write the reply text, ready to copy and send
- Request is meeting prep → produce the agenda, talking points, or briefing notes
- Request is a summary → write the summary
- Request is extracting action items → list them clearly and concisely
- Request is drafting a document → write the document
- Request is finding local options → list real options from the web search results provided

Rules:
- Do not use first person for things you cannot do ("I will call", "I will book", \
"I have sent"). You produce drafts and information; the user takes the physical action.
- Do not explain your reasoning or what you are about to do. Produce the output directly.
- Match the tone and format implied by the request and context.
- Only add salutation or sign-off when explicitly composing a letter or email reply. \
Never add "Best regards" or "[Your Name]" for conversational questions, schedule \
lookups, or task summaries.
- If the context is a message the user received and they want a reply drafted, \
the reply is addressed to the sender.
- Keep output concise and professional unless the task implies otherwise.
- CRITICAL: Never invent specific facts — business names, hotel names, addresses, phone \
numbers, prices, people, or events — that are not explicitly present in the provided \
context or web search results.
- CRITICAL: Calendar data is always pre-loaded into this prompt when available. Never ask \
the user to provide their calendar events — that is not information they can give you. \
If the calendar section says "(none in the next 7 days)", report that. If it says \
"(could not fetch)", say the calendar is temporarily unavailable. Never ask follow-up \
questions about calendar data.
- CRITICAL: Cards and tasks are work items — they do NOT have scheduled times and are NOT \
calendar events. Never infer or state a time for a task (e.g. "meeting at 2 PM") unless \
that exact time appears in the "### Upcoming calendar events" section. A task titled \
"Team meeting" means only that meeting prep is on the to-do list, not that any meeting \
is scheduled at a specific time.
- If location-specific information is needed but no location was given, say: \
"I don't have your location — please tell me where you are and I'll help from there."
- If web search results are provided, use them as the primary source of truth. \
Cite sources inline when useful (e.g. "Source: https://example.com").
- Do not use markdown formatting. No asterisks for bold, no # headers, no --- dividers. \
Use plain text with line breaks and simple punctuation for structure.
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
            max_tokens=150,
            temperature=0,
            timeout=10,
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
    except Exception:
        pass
    return ""


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


# ── Card thread endpoints ──────────────────────────────────────────────────────

def _get_or_none(db: Session, card_id: int) -> models.CardThread | None:
    return db.query(models.CardThread).filter_by(card_id=card_id).first()


@router.get("/api/cards/{card_id}/thread")
def get_thread(card_id: int, db: Session = Depends(get_db)):
    thread = _get_or_none(db, card_id)
    if thread is None:
        return {"card_id": card_id, "context": None, "messages": [], "output": None}
    return {
        "card_id":  card_id,
        "context":  thread.context,
        "messages": json.loads(thread.messages or "[]"),
        "output":   thread.output,
    }


@router.post("/api/cards/{card_id}/thread/message")
def send_message(request: Request, card_id: int, req: schemas.ThreadMessageRequest, db: Session = Depends(get_db)):
    """Stream a new assistant turn, then persist both user and assistant messages."""
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")

    thread = _get_or_none(db, card_id)
    history = json.loads(thread.messages or "[]") if thread else []
    context = thread.context if thread else None

    today = local_date(request)
    tz_offset = utc_offset_minutes(request)

    # Build system prompt — task identity + calendar context + pasted document
    system_parts = [_ASSIST_SYSTEM]
    system_parts.append(f"\nTask the user is working on: {card.title}")
    if card.description:
        system_parts.append(f"Task description: {card.description}")
    cal_lines = _calendar_context_lines(db, today, tz_offset)
    if cal_lines:
        system_parts.append("\n" + "\n".join(cal_lines))
    if context:
        system_parts.append(f"\nReference document provided by the user:\n{context}")
    system_prompt = "\n".join(system_parts)

    # Build LLM messages from history + new user turn
    llm_messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})
    llm_messages.append({"role": "user", "content": req.content})

    # Snapshot card_id for use inside the generator closure
    _card_id = card_id

    def generate():
        # Optional web search on the new user message
        search_ctx = ""
        search_ctx = _maybe_web_search(req.content)
        if search_ctx:
            yield f"data: {json.dumps({'status': 'searching'})}\n\n"
            # Inject search results into the last user message
            llm_messages[-1]["content"] += f"\n\n--- Web Search Results ---\n{search_ctx}"

        accumulated = ""
        try:
            stream = llm_client().chat.completions.create(
                model=LLM_MODEL,
                messages=llm_messages,
                stream=True,
                temperature=0.3,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    accumulated += delta
                    yield f"data: {json.dumps({'text': delta})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        # Persist user + assistant messages after streaming completes
        now = datetime.now(timezone.utc).isoformat()
        with SessionLocal() as save_db:
            t = save_db.query(models.CardThread).filter_by(card_id=_card_id).first()
            if t is None:
                t = models.CardThread(
                    card_id=_card_id,
                    messages="[]",
                    created_at=datetime.now(timezone.utc),
                )
                save_db.add(t)
            msgs = json.loads(t.messages or "[]")
            msgs.append({"role": "user",      "content": req.content,  "ts": now})
            msgs.append({"role": "assistant", "content": accumulated,  "ts": now})
            t.messages   = json.dumps(msgs)
            t.updated_at = datetime.now(timezone.utc)
            save_db.commit()

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.put("/api/cards/{card_id}/thread/context")
def update_context(card_id: int, req: schemas.ThreadContextRequest, db: Session = Depends(get_db)):
    thread = _get_or_none(db, card_id)
    if thread is None:
        thread = models.CardThread(
            card_id=card_id, messages="[]",
            created_at=datetime.now(timezone.utc),
        )
        db.add(thread)
    thread.context    = req.context or None
    thread.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.put("/api/cards/{card_id}/thread/output")
def save_output(card_id: int, req: schemas.ThreadOutputRequest, db: Session = Depends(get_db)):
    thread = _get_or_none(db, card_id)
    if thread is None:
        thread = models.CardThread(
            card_id=card_id, messages="[]",
            created_at=datetime.now(timezone.utc),
        )
        db.add(thread)
    thread.output     = req.output or None
    thread.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "output": thread.output}


@router.delete("/api/cards/{card_id}/thread")
def clear_thread(card_id: int, db: Session = Depends(get_db)):
    thread = _get_or_none(db, card_id)
    if thread:
        db.delete(thread)
        db.commit()
    return {"ok": True}


@router.post("/api/cards/{card_id}/thread/context-from")
def context_from(card_id: int, req: schemas.ContextFromRequest, db: Session = Depends(get_db)):
    """Generate context text from a structured source (section, tag, or similar cards)."""
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")

    cards = []
    label = ""

    if req.source == "section":
        section = req.section or "today"
        label_map = {"today": "Today", "week": "This Week", "month": "This Month", "later": "Stash"}
        label = label_map.get(section, section)
        cards = (
            db.query(models.Card)
            .filter(
                models.Card.section == section,
                models.Card.archived == False,   # noqa: E712
                models.Card.completed == False,  # noqa: E712
                models.Card.id != card_id,
            )
            .order_by(models.Card.position)
            .limit(20)
            .all()
        )

    elif req.source == "tag":
        if not req.tag_id:
            raise HTTPException(status_code=400, detail="tag_id required for tag source")
        tag = db.query(models.Tag).filter(models.Tag.id == req.tag_id).first()
        label = tag.name if tag else f"tag #{req.tag_id}"
        cards = (
            db.query(models.Card)
            .join(models.card_tags, models.Card.id == models.card_tags.c.card_id)
            .filter(
                models.card_tags.c.tag_id == req.tag_id,
                models.Card.archived == False,   # noqa: E712
                models.Card.completed == False,  # noqa: E712
                models.Card.id != card_id,
            )
            .limit(20)
            .all()
        )

    elif req.source == "similar":
        label = "Similar cards"
        try:
            import embeddings as _embeddings
            query_text = f"{card.title} {card.description or ''}".strip()
            card_ids = _embeddings.search(db, query_text, top_k=9)
            card_ids = [cid for cid in card_ids if cid != card_id][:8]
            if card_ids:
                id_order = {cid: i for i, cid in enumerate(card_ids)}
                cards = db.query(models.Card).filter(
                    models.Card.archived == False,  # noqa: E712
                    models.Card.id.in_(card_ids),
                ).all()
                cards.sort(key=lambda c: id_order.get(c.id, 999))
        except Exception:
            pass

    if not cards:
        return {"context_text": "", "label": label, "count": 0}

    lines = [f"### {label}"]
    for c in cards:
        line = f"- {c.title}"
        if c.description:
            snippet = c.description[:120].replace("\n", " ")
            line += f": {snippet}"
        lines.append(line)

    return {"context_text": "\n".join(lines), "label": label, "count": len(cards)}


# ── One-shot card assist (used by QuickAddModal) ───────────────────────────────

@router.post("/api/assist/stream")
def stream_assist(req: schemas.AssistRequest):
    location_str = None
    if req.lat is not None and req.lon is not None:
        location_str = _reverse_geocode(req.lat, req.lon)

    task_line = f"Task: {req.card_title}"
    if req.card_description:
        task_line += f"\nTask description: {req.card_description}"
    user_msg_parts = [task_line]
    if location_str:
        user_msg_parts.append(f"User's location: {location_str}")
    user_msg_parts.append(f"\nContext provided by user:\n{req.context}")
    user_msg = "\n".join(user_msg_parts)

    def generate():
        search_ctx = _maybe_web_search(user_msg)
        final_user = user_msg
        if search_ctx:
            yield f"data: {json.dumps({'status': 'searching'})}\n\n"
            final_user += f"\n\n--- Web Search Results ---\n{search_ctx}"

        try:
            stream = llm_client().chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": _ASSIST_SYSTEM},
                    {"role": "user",   "content": final_user},
                ],
                stream=True,
                temperature=0.3,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield f"data: {json.dumps({'text': delta})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Global assist (header-level, section/tag context) ─────────────────────────

@router.post("/api/assist/global")
def global_assist(request: Request, req: schemas.GlobalAssistRequest):
    """Header-level assistant: fetches card context by section or tag, then streams a response."""
    location_str = None
    if req.lat is not None and req.lon is not None:
        location_str = _reverse_geocode(req.lat, req.lon)

    today = local_date(request)
    tz_offset = utc_offset_minutes(request)

    card_context_parts: list[str] = []
    with SessionLocal() as db:
        if req.section:
            cards = (
                db.query(models.Card)
                .filter(
                    models.Card.section == req.section,
                    models.Card.archived == False,  # noqa: E712
                    models.Card.completed == False,  # noqa: E712
                )
                .order_by(models.Card.created_at)
                .limit(20)
                .all()
            )
            if cards:
                label_map = {
                    "today": "Today", "week": "This Week",
                    "month": "This Month", "later": "Stash",
                }
                label = label_map.get(req.section, req.section)
                card_context_parts.append(f"### Tasks (work items, not calendar events) — {label}")
                for c in cards:
                    card_context_parts.append(
                        f"- **{c.title}**" + (f": {c.description}" if c.description else "")
                    )
        elif req.tag_id:
            tag = db.query(models.Tag).filter(models.Tag.id == req.tag_id).first()
            tag_cards = (
                db.query(models.Card)
                .join(models.card_tags, models.Card.id == models.card_tags.c.card_id)
                .filter(
                    models.card_tags.c.tag_id == req.tag_id,
                    models.Card.archived == False,  # noqa: E712
                    models.Card.completed == False,  # noqa: E712
                )
                .limit(20)
                .all()
            )
            if tag_cards:
                label = tag.name if tag else f"tag #{req.tag_id}"
                card_context_parts.append(f"### Tasks tagged '{label}' (work items, not calendar events)")
                for c in tag_cards:
                    card_context_parts.append(
                        f"- **{c.title}**" + (f": {c.description}" if c.description else "")
                    )
        else:
            # No section/tag filter — use semantic search to find relevant cards and GitHub items
            try:
                import embeddings as _embeddings

                card_ids = _embeddings.search(db, req.prompt, top_k=8)
                if card_ids:
                    id_order = {cid: i for i, cid in enumerate(card_ids)}
                    sem_cards = db.query(models.Card).filter(
                        models.Card.archived == False,  # noqa: E712
                        models.Card.id.in_(card_ids),
                    ).all()
                    sem_cards.sort(key=lambda c: id_order.get(c.id, 999))
                    if sem_cards:
                        card_context_parts.append("### Potentially relevant tasks (work items, not calendar events)")
                        for c in sem_cards:
                            card_context_parts.append(
                                f"- **{c.title}**" + (f": {c.description}" if c.description else "")
                            )

                eng_ids = _embeddings.search_eng(db, req.prompt, top_k=5)
                if eng_ids:
                    id_order = {iid: i for i, iid in enumerate(eng_ids)}
                    sem_eng = db.query(models.EngineeringItem).filter(
                        models.EngineeringItem.id.in_(eng_ids),
                    ).all()
                    sem_eng.sort(key=lambda e: id_order.get(e.id, 999))
                    if sem_eng:
                        card_context_parts.append("### Potentially relevant GitHub items")
                        for e in sem_eng:
                            status = f" ({e.project_status})" if e.project_status else ""
                            card_context_parts.append(f"- **{e.title}**{status} — {e.repo} [{e.state}]")
            except Exception:
                pass

        # Calendar events (next 7 days)
        cal_lines = _calendar_context_lines(db, today, tz_offset)
        if cal_lines:
            card_context_parts.extend(cal_lines)

        background_parts: list[str] = []
        today_str = today.isoformat()
        _, health_ctx = build_health_context(db, today)
        if health_ctx:
            background_parts.append(health_ctx)
        all_habits = db.query(models.Habit).filter(models.Habit.archived == False).all()  # noqa: E712
        completed_habit_ids = {
            c.habit_id for c in db.query(models.HabitCompletion)
            .filter(models.HabitCompletion.date == today_str).all()
        }
        pending_habits = [h.name for h in all_habits if h.id not in completed_habit_ids]
        done_habits    = [h.name for h in all_habits if h.id in completed_habit_ids]
        if pending_habits:
            background_parts.append("Habits pending today: " + ", ".join(pending_habits))
        if done_habits:
            background_parts.append("Habits completed today: " + ", ".join(done_habits))

    user_msg_parts = [f"Request: {req.prompt}"]
    if location_str:
        user_msg_parts.append(f"User's location: {location_str}")
    if background_parts:
        user_msg_parts.append("\n## User context (today)\n" + "\n".join(background_parts))
    if card_context_parts:
        user_msg_parts.append("\n" + "\n".join(card_context_parts))
    user_msg = "\n".join(user_msg_parts)

    def generate():
        search_ctx = _maybe_web_search(user_msg)
        final_user = user_msg
        if search_ctx:
            yield f"data: {json.dumps({'status': 'searching'})}\n\n"
            final_user += f"\n\n--- Web Search Results ---\n{search_ctx}"

        try:
            stream = llm_client().chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": _ASSIST_SYSTEM},
                    {"role": "user",   "content": final_user},
                ],
                stream=True,
                temperature=0.3,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield f"data: {json.dumps({'text': delta})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
