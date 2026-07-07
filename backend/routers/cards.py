import calendar as _calendar
import json
import random
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
import schemas
from card_sections import CardSection
from deps import get_db, llm_client, LLM_MODEL, local_date
from model_plugins import get_plugin
from model_plugins.base import resolve_dates
from routers.insights import invalidate_insights_cache

router = APIRouter()

_SECTION_ORDER = {
    CardSection.TODAY: 0,
    CardSection.WEEK:  1,
    CardSection.MONTH: 2,
    CardSection.LATER: 3,
}


def _auto_migrate_sections(db: Session, today) -> None:
    """Advance cards with scheduled_at into the correct section based on today's date.
    Only moves forward (e.g. week → today); never pushes a card to a later section."""
    cards = (
        db.query(models.Card)
        .filter(
            models.Card.completed == False,  # noqa: E712
            models.Card.scheduled_at.isnot(None),
        )
        .all()
    )
    changed = False
    for card in cards:
        delta = (card.scheduled_at.date() - today).days
        if delta <= 0:
            target = "today"
        elif delta <= 7:
            target = "week"
        elif delta <= 30:
            target = "month"
        else:
            target = "later"
        if _SECTION_ORDER[target] < _SECTION_ORDER.get(card.section, 3):
            if target == "today" and card.section != "today":
                card.today_since = datetime.now(timezone.utc)
            card.section = target
            changed = True
    if changed:
        db.commit()


def _next_occurrence(base: datetime, rule: str) -> datetime:
    rule = rule.lower().strip()
    if rule == "daily":
        return base + timedelta(days=1)
    if rule == "weekly":
        return base + timedelta(weeks=1)
    if rule == "monthly":
        month = base.month % 12 + 1
        year = base.year + (1 if base.month == 12 else 0)
        day = min(base.day, _calendar.monthrange(year, month)[1])
        return base.replace(year=year, month=month, day=day)
    if rule == "yearly":
        return base.replace(year=base.year + 1)
    return base + timedelta(weeks=1)


def _section_for_date(d, today) -> str:
    delta = (d - today).days
    if delta <= 0:
        return "today"
    if delta <= 7:
        return "week"
    if delta <= 30:
        return "month"
    return "later"


@router.get("/api/cards/search", response_model=List[schemas.Card])
def search_cards(q: str = Query(default="", min_length=1), db: Session = Depends(get_db)):
    pattern = f"%{q}%"
    return (
        db.query(models.Card)
        .filter(
            models.Card.archived == False,  # noqa: E712
            or_(
                models.Card.title.ilike(pattern),
                models.Card.description.ilike(pattern),
            )
        )
        .order_by(models.Card.completed, models.Card.section, models.Card.position)
        .limit(30)
        .all()
    )


@router.get("/api/cards", response_model=List[schemas.Card])
def get_cards(request: Request, db: Session = Depends(get_db)):
    _auto_migrate_sections(db, local_date(request))
    return (
        db.query(models.Card)
        .order_by(models.Card.section, models.Card.position)
        .all()
    )


@router.post("/api/cards", response_model=schemas.Card, status_code=201)
def create_card(card: schemas.CardCreate, db: Session = Depends(get_db)):
    count = db.query(models.Card).filter(models.Card.section == card.section).count()
    data = card.model_dump()
    tag_ids = data.pop("tag_ids", [])
    now = datetime.now(timezone.utc)
    db_card = models.Card(**data, position=count, updated_at=now)
    if db_card.section == "today":
        db_card.today_since = now
    if tag_ids:
        db_card.tags = db.query(models.Tag).filter(models.Tag.id.in_(tag_ids)).all()
    db.add(db_card)
    db.commit()
    db.refresh(db_card)
    return db_card


@router.post("/api/cards/reorder")
def reorder_cards(updates: List[schemas.CardReorderItem], db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    for item in updates:
        db_card = db.query(models.Card).filter(models.Card.id == item.id).first()
        if db_card:
            if item.section == "today" and db_card.section != "today":
                db_card.today_since = now
            elif item.section != "today":
                db_card.today_since = None
            db_card.section = item.section
            db_card.position = item.position
    db.commit()
    return {"ok": True}


@router.post("/api/cards/parse", response_model=schemas.ParsedTodo)
def parse_card(request: Request, req: schemas.ParseRequest, db: Session = Depends(get_db)):
    today = local_date(request)
    tomorrow = today + timedelta(days=1)
    tag_names = [t.name for t in db.query(models.Tag).order_by(models.Tag.name).all()]
    tags_section = (
        f"Available tags: {', '.join(tag_names)}"
        if tag_names
        else "No tags available."
    )
    plugin = get_plugin(LLM_MODEL)
    prompt = plugin.get_system_prompt(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        tomorrow=tomorrow.isoformat(),
        tags_section=tags_section,
    )
    try:
        client = llm_client()
        response = client.chat.completions.create(
            model=plugin.model_name,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": req.text},
            ],
        )
        raw = plugin.normalize_raw(json.loads(response.choices[0].message.content))
        parsed = plugin.post_process(schemas.ParsedTodo.model_validate(raw), text=req.text)
        parsed = resolve_dates(parsed, text=req.text, today=today)
        return parsed
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"LLM request failed ({LLM_MODEL}): {e}",
        )


@router.post("/api/cards/parse-bulk", response_model=schemas.BulkParseResponse)
def parse_bulk(request: Request, req: schemas.ParseRequest, db: Session = Depends(get_db)):
    today = local_date(request)
    tomorrow = today + timedelta(days=1)
    tag_names = [t.name for t in db.query(models.Tag).order_by(models.Tag.name).all()]
    tags_section = (
        f"Available tags: {', '.join(tag_names)}"
        if tag_names
        else "No tags available."
    )
    plugin = get_plugin(LLM_MODEL)
    prompt = plugin.get_bulk_system_prompt(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        tomorrow=tomorrow.isoformat(),
        tags_section=tags_section,
    )
    try:
        client = llm_client()
        response = client.chat.completions.create(
            model=plugin.model_name,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": req.text},
            ],
        )
        data = json.loads(response.choices[0].message.content)
        raw_items = data.get("items", [])
        lines = [l.strip() for l in req.text.splitlines() if l.strip()]
        items = []
        for i, raw in enumerate(raw_items):
            raw_title = raw.get("title", "") if isinstance(raw.get("title"), str) else ""
            raw = plugin.normalize_raw(raw)
            line = lines[i] if i < len(lines) else ""
            if len(lines) == len(raw_items):
                date_hint = line or raw_title
            else:
                date_hint = raw_title
            parsed = plugin.post_process(schemas.ParsedTodo.model_validate(raw), text=date_hint)
            parsed = resolve_dates(parsed, text=date_hint, today=today)
            if (parsed.source_text and not parsed.description
                    and parsed.source_text.lower().strip() != parsed.title.lower().strip()):
                parsed.description = parsed.source_text
            items.append(parsed)
        return schemas.BulkParseResponse(items=items)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"LLM request failed ({LLM_MODEL}): {e}",
        )


@router.post("/api/shortcut/add")
def shortcut_add(request: Request, req: schemas.ParseRequest, db: Session = Depends(get_db)):
    """Parse free-text and create a card in one step. Designed for iOS Shortcuts."""
    today = local_date(request)
    tomorrow = today + timedelta(days=1)
    tag_names = [t.name for t in db.query(models.Tag).order_by(models.Tag.name).all()]
    tags_section = (
        f"Available tags: {', '.join(tag_names)}" if tag_names else "No tags available."
    )
    plugin = get_plugin(LLM_MODEL)
    prompt = plugin.get_system_prompt(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        tomorrow=tomorrow.isoformat(),
        tags_section=tags_section,
    )
    try:
        client = llm_client()
        response = client.chat.completions.create(
            model=plugin.model_name,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": req.text},
            ],
        )
        raw = plugin.normalize_raw(json.loads(response.choices[0].message.content))
        parsed = plugin.post_process(schemas.ParsedTodo.model_validate(raw), text=req.text)
        parsed = resolve_dates(parsed, text=req.text, today=today)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM request failed: {e}")

    # Resolve tag names → IDs
    tag_ids = []
    if parsed.suggested_tags:
        tag_rows = db.query(models.Tag).filter(models.Tag.name.in_(parsed.suggested_tags)).all()
        tag_ids = [t.id for t in tag_rows]

    section = parsed.section or "today"
    count = db.query(models.Card).filter(models.Card.section == section).count()
    now = datetime.now(timezone.utc)
    db_card = models.Card(
        title=parsed.title,
        description=parsed.description,
        section=section,
        scheduled_at=parsed.scheduled_at,
        position=count,
        updated_at=now,
    )
    if tag_ids:
        db_card.tags = db.query(models.Tag).filter(models.Tag.id.in_(tag_ids)).all()
    db.add(db_card)
    db.commit()
    db.refresh(db_card)
    return {"ok": True, "id": db_card.id, "title": db_card.title, "section": db_card.section}


@router.put("/api/cards/{card_id}", response_model=schemas.Card)
def update_card(request: Request, card_id: int, card: schemas.CardUpdate, db: Session = Depends(get_db)):
    db_card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not db_card:
        raise HTTPException(status_code=404, detail="Card not found")
    data = card.model_dump(exclude_unset=True)
    tag_ids = data.pop("tag_ids", None)
    completing = data.get("completed") and not db_card.completed
    now = datetime.now(timezone.utc)
    if "completed" in data:
        if completing:
            db_card.completed_at = now
        elif not data["completed"]:
            db_card.completed_at = None
    if "archived" in data:
        data["archived_at"] = now if data["archived"] else None
    new_section = data.get("section")
    if new_section == "today" and db_card.section != "today":
        data["today_since"] = now
    elif new_section is not None and new_section != "today":
        data["today_since"] = None
    for key, value in data.items():
        setattr(db_card, key, value)
    db_card.updated_at = now
    if tag_ids is not None:
        db_card.tags = db.query(models.Tag).filter(models.Tag.id.in_(tag_ids)).all()

    # Spawn next occurrence when completing a recurring card
    if completing and db_card.recurrence_rule:
        base = db_card.scheduled_at or now
        next_dt = _next_occurrence(base, db_card.recurrence_rule)
        next_section = _section_for_date(next_dt.date(), local_date(request))
        count = db.query(models.Card).filter(models.Card.section == next_section).count()
        next_card = models.Card(
            title=db_card.title,
            body=db_card.body,
            description=db_card.description,
            section=next_section,
            scheduled_at=next_dt,
            recurrence_rule=db_card.recurrence_rule,
            position=count,
            tags=list(db_card.tags),
            updated_at=now,
        )
        db.add(next_card)

    db.commit()
    db.refresh(db_card)
    invalidate_insights_cache()
    return db_card


@router.post("/api/cards/{card_id}/tags/{tag_id}")
def add_tag_to_card(card_id: int, tag_id: int, db: Session = Depends(get_db)):
    db_card = db.query(models.Card).filter(models.Card.id == card_id).first()
    db_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not db_card or not db_tag:
        raise HTTPException(status_code=404, detail="Not found")
    if db_tag not in db_card.tags:
        db_card.tags.append(db_tag)
        db.commit()
    return {"ok": True}


@router.delete("/api/cards/{card_id}/tags/{tag_id}")
def remove_tag_from_card(card_id: int, tag_id: int, db: Session = Depends(get_db)):
    db_card = db.query(models.Card).filter(models.Card.id == card_id).first()
    db_tag = db.query(models.Tag).filter(models.Tag.id == tag_id).first()
    if not db_card or not db_tag:
        raise HTTPException(status_code=404, detail="Not found")
    if db_tag in db_card.tags:
        db_card.tags.remove(db_tag)
        db.commit()
    return {"ok": True}


_PROJECT_TAG_COLORS = [
    "#7c3aed",  # purple
    "#2563eb",  # blue
    "#059669",  # green
    "#d97706",  # amber
    "#dc2626",  # red
    "#0891b2",  # cyan
    "#c026d3",  # fuchsia
    "#65a30d",  # lime
    "#ea580c",  # orange
    "#db2777",  # pink
]

_BREAKDOWN_SYSTEM = """\
Break down the following task into 3 to 6 ordered, specific, actionable subtasks.
Also suggest a short project name (2–4 words, no "Project:" prefix).
Return ONLY a JSON object — no markdown, no explanation.
Example: {"project_name": "Brunch Planning", "subtasks": ["Research venues", "Send invitations", "Buy supplies"]}
"""


@router.post("/api/cards/{card_id}/breakdown")
def breakdown_card(card_id: int, db: Session = Depends(get_db)):
    """Call LLM to suggest subtasks. Returns preview only — no DB changes."""
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    prompt = card.title
    if card.description:
        prompt += f"\n\nContext: {card.description}"
    try:
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _BREAKDOWN_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
        )
        raw = resp.choices[0].message.content.strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Expected object")
        project_name = str(parsed.get("project_name", card.title)).strip()
        subtasks = parsed.get("subtasks", [])
        if not isinstance(subtasks, list):
            raise ValueError("Expected subtasks list")
        subtasks = [s.strip() for s in subtasks if isinstance(s, str) and s.strip()]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM request failed: {e}")
    return {"subtasks": subtasks, "tag_name": f"Project: {project_name}"}


@router.post("/api/cards/bulk")
def bulk_create_cards(req: schemas.BulkCardCreate, db: Session = Depends(get_db)):
    """Create multiple cards atomically. All succeed or all fail."""
    now = datetime.now(timezone.utc)
    try:
        created = []
        for item in req.cards:
            base_pos = db.query(models.Card).filter(models.Card.section == item.section).count()
            c = models.Card(title=item.title.strip(), section=item.section,
                            position=base_pos, completed=False, updated_at=now)
            db.add(c)
            db.flush()
            created.append(c)
        db.commit()
        for c in created:
            db.refresh(c)
        return {"cards": [schemas.Card.model_validate(c) for c in created]}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create cards")


@router.post("/api/cards/{card_id}/breakdown/commit")
def commit_breakdown(card_id: int, req: schemas.BreakdownCommit, db: Session = Depends(get_db)):
    """Create project tag + subtask cards and archive the original card — all in one transaction."""
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    try:
        tag_name = req.tag_name
        tag = db.query(models.Tag).filter(models.Tag.name == tag_name).first()
        if not tag:
            used_colors = {row[0] for row in db.query(models.Tag.color).all()}
            available = [c for c in _PROJECT_TAG_COLORS if c not in used_colors]
            color = available[0] if available else random.choice(_PROJECT_TAG_COLORS)
            tag = models.Tag(name=tag_name, color=color)
            db.add(tag)
            db.flush()

        now = datetime.now(timezone.utc)
        base_pos = db.query(models.Card).filter(models.Card.section == card.section).count()
        created = []
        for i, title in enumerate(req.subtasks):
            t = title.strip()
            if not t:
                continue
            c = models.Card(title=t, section=card.section, position=base_pos + i,
                            completed=False, updated_at=now,
                            today_since=now if card.section == "today" else None)
            c.tags = [tag]
            db.add(c)
            db.flush()
            created.append(c)

        card.archived = True
        card.archived_at = now
        card.updated_at = now
        if tag not in card.tags:
            card.tags.append(tag)

        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to commit breakdown")

    for c in created:
        db.refresh(c)
    db.refresh(card)
    db.refresh(tag)

    return {
        "tag": {"id": tag.id, "name": tag.name, "color": tag.color},
        "cards": [schemas.Card.model_validate(c) for c in created],
        "archived_card": schemas.Card.model_validate(card),
    }


@router.delete("/api/cards/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db)):
    db_card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not db_card:
        raise HTTPException(status_code=404, detail="Card not found")
    db.delete(db_card)
    db.commit()
    return {"ok": True}
