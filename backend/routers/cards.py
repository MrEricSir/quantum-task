import calendar as _calendar
import io
import json
import os
import plistlib
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db, llm_client, LLM_MODEL, local_date, AUTH_PASSWORD
from model_plugins import get_plugin
from model_plugins.base import resolve_dates

_ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:8000")


def _build_shortcut_plist(base_url: str, password: str) -> bytes:
    """Generate a pre-configured iOS .shortcut binary plist."""
    ORC = "\uFFFC"  # Object Replacement Character — variable placeholder
    ask_uuid = str(_uuid.uuid4()).upper()
    post_uuid = str(_uuid.uuid4()).upper()

    actions = [
        # 1. Ask for text
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.ask",
            "WFWorkflowActionParameters": {
                "WFAskActionPrompt": "Add a task:",
                "WFInputType": "Text",
                "UUID": ask_uuid,
            },
        },
        # 2. POST to /api/shortcut/add
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "WFHTTPMethod": "POST",
                "WFURL": f"{base_url}/api/shortcut/add",
                "WFHTTPBodyType": "JSON",
                "WFHTTPHeaders": {
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            {
                                "WFKey": {
                                    "Value": {"string": "Authorization"},
                                    "WFSerializationType": "WFTextTokenString",
                                },
                                "WFValue": {
                                    "Value": {"string": f"Bearer {password}"},
                                    "WFSerializationType": "WFTextTokenString",
                                },
                                "WFItemType": 0,
                            }
                        ]
                    },
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
                "WFJSONValues": {
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            {
                                "WFKey": {
                                    "Value": {"string": "text"},
                                    "WFSerializationType": "WFTextTokenString",
                                },
                                "WFValue": {
                                    "Value": {
                                        "string": ORC,
                                        "attachmentsByRange": {
                                            "{0, 1}": {
                                                "OutputUUID": ask_uuid,
                                                "Type": "ActionOutput",
                                                "OutputName": "Provided Input",
                                            }
                                        },
                                    },
                                    "WFSerializationType": "WFTextTokenString",
                                },
                                "WFItemType": 0,
                            }
                        ]
                    },
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
                "UUID": post_uuid,
            },
        },
        # 3. Show "Added: <title>" from JSON response
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
            "WFWorkflowActionParameters": {
                "Text": {
                    "Value": {
                        "string": f"Added: {ORC}",
                        "attachmentsByRange": {
                            "{7, 1}": {
                                "OutputUUID": post_uuid,
                                "Type": "ActionOutput",
                                "OutputName": "Contents of URL",
                            }
                        },
                    },
                    "WFSerializationType": "WFTextTokenString",
                }
            },
        },
    ]

    data = {
        "WFWorkflowClientVersion": "1326.0.4",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowActions": actions,
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowTypes": ["NCWidget"],
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 4282601983,  # purple
            "WFWorkflowIconGlyphNumber": 59511,
        },
    }

    buf = io.BytesIO()
    plistlib.dump(data, buf, fmt=plistlib.FMT_BINARY)
    return buf.getvalue()

router = APIRouter()

_SECTION_ORDER = {"today": 0, "week": 1, "month": 2, "later": 3}


def _auto_migrate_sections(db: Session, today) -> None:
    """Advance cards with scheduled_at into the correct section based on today's date.
    Only moves forward (e.g. week → today); never pushes a card to a later section."""
    cards = (
        db.query(models.Card)
        .filter(
            models.Card.completed == False,  # noqa: E712
            models.Card.scheduled_at.isnot(None),
            models.Card.section != "none",  # reference cards are never auto-migrated
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
    if tag_ids:
        db_card.tags = db.query(models.Tag).filter(models.Tag.id.in_(tag_ids)).all()
    db.add(db_card)
    db.commit()
    db.refresh(db_card)
    return db_card


@router.post("/api/cards/reorder")
def reorder_cards(updates: List[schemas.CardReorderItem], db: Session = Depends(get_db)):
    for item in updates:
        db_card = db.query(models.Card).filter(models.Card.id == item.id).first()
        if db_card:
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


@router.get("/api/shortcut/download")
def shortcut_download():
    """Serve a pre-configured iOS .shortcut file for adding tasks."""
    plist_bytes = _build_shortcut_plist(_ALLOWED_ORIGIN, AUTH_PASSWORD)
    return Response(
        content=plist_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="Add to Quantum Task.shortcut"'},
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


@router.delete("/api/cards/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db)):
    db_card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not db_card:
        raise HTTPException(status_code=404, detail="Card not found")
    db.delete(db_card)
    db.commit()
    return {"ok": True}
