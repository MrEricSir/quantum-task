"""
Assistant FastAPI endpoints — thin HTTP adapters only.

- /api/cards/{id}/thread  — persistent multi-turn conversation attached to a card
- /api/assist/stream      — one-shot card assist (legacy, still used by QuickAddModal)
- /api/assist/global      — header-level assistant with section/tag context

Business logic lives in assist.generate and assist.context.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.requests import Request

import models
import schemas
from assist.generate import (
    context_from as _context_from,
    generate_spec as _generate_spec,
    global_assist as _global_assist,
    send_message as _send_message,
    stream_assist as _stream_assist,
)
from deps import get_db

router = APIRouter()


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
    return _send_message(request, card_id, req, db)


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


@router.post("/api/cards/{card_id}/spec/generate")
def generate_spec(card_id: int, db: Session = Depends(get_db)):
    """Synthesize a structured implementation spec from the card's GitHub issue + thread."""
    return _generate_spec(card_id, db)


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
    return _context_from(card_id, req, db)


# ── One-shot card assist (used by QuickAddModal) ───────────────────────────────

@router.post("/api/assist/stream")
def stream_assist(req: schemas.AssistRequest):
    return _stream_assist(req)


# ── Global assist (header-level, section/tag context) ─────────────────────────

@router.post("/api/assist/global")
def global_assist(request: Request, req: schemas.GlobalAssistRequest):
    """Header-level assistant: fetches card context by section or tag, then streams a response."""
    return _global_assist(request, req)
