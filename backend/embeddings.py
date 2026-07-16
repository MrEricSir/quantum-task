"""
Card embeddings for semantic search.

Vectors are generated via an OpenAI-compatible /v1/embeddings endpoint
(EMBEDDING_BASE_URL + EMBEDDING_MODEL env vars; falls back to LLM_BASE_URL).
Stored as JSON in the card_embeddings table; cosine similarity computed in Python.

All public functions fail silently if the embedding endpoint is unavailable,
so the rest of the app is never blocked.
"""
import json
import logging
import math
import os
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

EMBED_BASE_URL = os.environ.get("EMBEDDING_BASE_URL") or os.environ.get("LLM_BASE_URL", "")
EMBED_API_KEY  = os.environ.get("EMBEDDING_API_KEY")  or os.environ.get("LLM_API_KEY", "")
EMBED_MODEL    = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")


def _embed(text: str) -> list[float] | None:
    if not EMBED_BASE_URL:
        return None
    try:
        r = requests.post(
            f"{EMBED_BASE_URL.rstrip('/')}/embeddings",
            json={"input": text[:4000], "model": EMBED_MODEL},
            headers={
                "Authorization": f"Bearer {EMBED_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    except Exception as e:
        log.warning("embedding request failed: %s", e)
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _card_text(title: str, description: str | None) -> str:
    return f"{title} {description}".strip() if description else title


# ── DB operations ─────────────────────────────────────────────────────────────

def upsert(db, card_id: int, title: str, description: str | None) -> None:
    """Embed a card and store/update its vector. No-op if embedding fails."""
    import models
    vec = _embed(_card_text(title, description))
    if not vec:
        return
    now = datetime.now(timezone.utc)
    row = db.query(models.CardEmbedding).filter_by(card_id=card_id).first()
    if row:
        row.embedding  = json.dumps(vec)
        row.updated_at = now
    else:
        db.add(models.CardEmbedding(card_id=card_id, embedding=json.dumps(vec), updated_at=now))
    db.commit()


def upsert_bg(card_id: int, title: str, description: str | None) -> None:
    """Background-task version — creates its own DB session."""
    from database import SessionLocal
    with SessionLocal() as db:
        upsert(db, card_id, title, description)


def delete(db, card_id: int) -> None:
    import models
    db.query(models.CardEmbedding).filter_by(card_id=card_id).delete()
    db.commit()


def search(db, query: str, top_k: int = 5) -> list[int]:
    """Return card IDs ordered by cosine similarity. Empty list if unavailable."""
    import models
    q_vec = _embed(query)
    if not q_vec:
        return []
    rows = db.query(models.CardEmbedding).all()
    scored: list[tuple[int, float]] = []
    for row in rows:
        try:
            vec = json.loads(row.embedding)
            scored.append((row.card_id, _cosine(q_vec, vec)))
        except Exception:
            pass
    scored.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in scored[:top_k]]


def _eng_text(title: str, repo: str) -> str:
    return f"{title} [{repo}]"


def upsert_eng(db, item_id: int, title: str, repo: str) -> None:
    """Embed a GitHub engineering item and store/update its vector."""
    import models
    vec = _embed(_eng_text(title, repo))
    if not vec:
        return
    now = datetime.now(timezone.utc)
    row = db.query(models.EngineeringItemEmbedding).filter_by(item_id=item_id).first()
    if row:
        row.embedding  = json.dumps(vec)
        row.updated_at = now
    else:
        db.add(models.EngineeringItemEmbedding(item_id=item_id, embedding=json.dumps(vec), updated_at=now))
    db.commit()


def upsert_eng_bg(item_id: int, title: str, repo: str) -> None:
    """Background-task version — creates its own DB session."""
    from database import SessionLocal
    with SessionLocal() as db:
        upsert_eng(db, item_id, title, repo)


def delete_eng(db, item_id: int) -> None:
    import models
    db.query(models.EngineeringItemEmbedding).filter_by(item_id=item_id).delete()
    db.commit()


def search_eng(db, query: str, top_k: int = 5) -> list[int]:
    """Return engineering item IDs ordered by cosine similarity. Empty list if unavailable."""
    import models
    q_vec = _embed(query)
    if not q_vec:
        return []
    rows = db.query(models.EngineeringItemEmbedding).all()
    scored: list[tuple[int, float]] = []
    for row in rows:
        try:
            vec = json.loads(row.embedding)
            scored.append((row.item_id, _cosine(q_vec, vec)))
        except Exception:
            pass
    scored.sort(key=lambda x: x[1], reverse=True)
    return [iid for iid, _ in scored[:top_k]]


# ── Startup backfill ──────────────────────────────────────────────────────────

def backfill() -> None:
    """Embed any non-archived cards and open engineering items that lack vectors. Run at startup."""
    from database import SessionLocal
    import models
    with SessionLocal() as db:
        # Cards
        embedded_ids = {r.card_id for r in db.query(models.CardEmbedding.card_id).all()}
        cards = (
            db.query(models.Card)
            .filter(
                models.Card.archived == False,  # noqa: E712
                models.Card.id.notin_(embedded_ids),
            )
            .limit(500)
            .all()
        )
        for card in cards:
            upsert(db, card.id, card.title, card.description)
        if cards:
            log.info("embeddings: backfilled %d cards", len(cards))

        # Engineering items
        embedded_eng_ids = {r.item_id for r in db.query(models.EngineeringItemEmbedding.item_id).all()}
        eng_items = (
            db.query(models.EngineeringItem)
            .filter(
                models.EngineeringItem.state == "open",
                models.EngineeringItem.id.notin_(embedded_eng_ids),
            )
            .limit(200)
            .all()
        )
        for item in eng_items:
            upsert_eng(db, item.id, item.title, item.repo)
        if eng_items:
            log.info("embeddings: backfilled %d engineering items", len(eng_items))
