"""
Unit tests for embeddings.py — semantic search module.

All tests run without a real embedding endpoint by patching _embed to return
deterministic fake vectors. No network, no LLM, no Ollama required.
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import math
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import embeddings as emb


# ── DB fixture ────────────────────────────────────────────────────────────────

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def db():
    models.Base.metadata.create_all(bind=engine)
    yield
    models.Base.metadata.drop_all(bind=engine)


@pytest.fixture
def session():
    s = Session()
    yield s
    s.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _unit(v):
    """Return a unit vector in the direction of v (list of floats)."""
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v]


def _card(session, title, description=None, section="today", archived=False):
    c = models.Card(title=title, description=description, section=section,
                    archived=archived, position=0)
    session.add(c)
    session.commit()
    return c


def _eng_item(session, title, repo="owner/repo", state="open"):
    e = models.EngineeringItem(
        external_id=f"github:{repo}/{title}",
        title=title, item_type="issue", repo=repo,
        number=1, url=f"https://github.com/{repo}/issues/1",
        state=state, synced_at=datetime.now(timezone.utc),
    )
    session.add(e)
    session.commit()
    return e


# ── _cosine ───────────────────────────────────────────────────────────────────

class TestCosine:

    def test_identical_vectors_return_one(self):
        v = _unit([1.0, 2.0, 3.0])
        assert abs(emb._cosine(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(emb._cosine(a, b)) < 1e-6

    def test_opposite_vectors_return_minus_one(self):
        v = _unit([1.0, 2.0, 3.0])
        neg = [-x for x in v]
        assert abs(emb._cosine(v, neg) - (-1.0)) < 1e-6

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        assert emb._cosine(a, b) == 0.0

    def test_similarity_order(self):
        query = _unit([1.0, 0.0, 0.0])
        close  = _unit([0.9, 0.1, 0.0])
        far    = _unit([0.0, 0.0, 1.0])
        assert emb._cosine(query, close) > emb._cosine(query, far)


# ── upsert / delete (cards) ───────────────────────────────────────────────────

class TestUpsertCard:

    def test_upsert_stores_embedding(self, session):
        c = _card(session, "Deploy the backend")
        fake_vec = _unit([1.0, 0.0, 0.0])
        with patch("embeddings._embed", return_value=fake_vec):
            emb.upsert(session, c.id, c.title, c.description)
        row = session.query(models.CardEmbedding).filter_by(card_id=c.id).first()
        assert row is not None
        assert json.loads(row.embedding) == pytest.approx(fake_vec)

    def test_upsert_updates_existing_row(self, session):
        c = _card(session, "Deploy the backend")
        v1 = _unit([1.0, 0.0, 0.0])
        v2 = _unit([0.0, 1.0, 0.0])
        with patch("embeddings._embed", return_value=v1):
            emb.upsert(session, c.id, c.title, None)
        with patch("embeddings._embed", return_value=v2):
            emb.upsert(session, c.id, "Updated title", None)
        rows = session.query(models.CardEmbedding).filter_by(card_id=c.id).all()
        assert len(rows) == 1
        assert json.loads(rows[0].embedding) == pytest.approx(v2)

    def test_upsert_no_op_when_embed_fails(self, session):
        c = _card(session, "Deploy the backend")
        with patch("embeddings._embed", return_value=None):
            emb.upsert(session, c.id, c.title, None)
        assert session.query(models.CardEmbedding).count() == 0

    def test_delete_removes_row(self, session):
        c = _card(session, "Deploy the backend")
        with patch("embeddings._embed", return_value=_unit([1.0, 0.0])):
            emb.upsert(session, c.id, c.title, None)
        emb.delete(session, c.id)
        assert session.query(models.CardEmbedding).filter_by(card_id=c.id).first() is None


# ── search (cards) ────────────────────────────────────────────────────────────

class TestSearchCards:

    def test_returns_empty_when_embed_fails(self, session):
        _card(session, "Some card")
        with patch("embeddings._embed", return_value=None):
            result = emb.search(session, "query")
        assert result == []

    def test_returns_empty_when_no_embeddings(self, session):
        _card(session, "Some card")
        with patch("embeddings._embed", return_value=_unit([1.0, 0.0])):
            result = emb.search(session, "query")
        assert result == []

    def test_returns_most_similar_card_first(self, session):
        c1 = _card(session, "Authentication bug")
        c2 = _card(session, "Billing invoice")
        c3 = _card(session, "Auth token refresh")

        # c1 and c3 are "authentication-like" (close to query); c2 is "billing"
        auth_vec    = _unit([1.0, 0.0])
        billing_vec = _unit([0.0, 1.0])

        def fake_embed(text):
            if "Auth" in text or "auth" in text.lower():
                return auth_vec
            return billing_vec

        with patch("embeddings._embed", side_effect=fake_embed):
            emb.upsert(session, c1.id, c1.title, None)
            emb.upsert(session, c2.id, c2.title, None)
            emb.upsert(session, c3.id, c3.title, None)

        with patch("embeddings._embed", return_value=auth_vec):
            result = emb.search(session, "authentication", top_k=3)

        assert c1.id in result
        assert c3.id in result
        # billing card should be ranked last
        assert result.index(c1.id) < result.index(c2.id) or result.index(c3.id) < result.index(c2.id)

    def test_top_k_limits_results(self, session):
        cards = [_card(session, f"Card {i}") for i in range(5)]
        v = _unit([1.0, 0.0])
        with patch("embeddings._embed", return_value=v):
            for c in cards:
                emb.upsert(session, c.id, c.title, None)
            result = emb.search(session, "query", top_k=3)
        assert len(result) == 3

    def test_corrupted_embedding_row_is_skipped(self, session):
        c = _card(session, "Good card")
        v = _unit([1.0, 0.0])
        with patch("embeddings._embed", return_value=v):
            emb.upsert(session, c.id, c.title, None)
        # Corrupt the row
        row = session.query(models.CardEmbedding).filter_by(card_id=c.id).first()
        row.embedding = "not valid json{"
        session.commit()
        with patch("embeddings._embed", return_value=v):
            result = emb.search(session, "query")
        assert result == []


# ── upsert_eng / delete_eng / search_eng ─────────────────────────────────────

class TestEngineeringEmbeddings:

    def test_upsert_eng_stores_embedding(self, session):
        e = _eng_item(session, "Fix auth token expiry")
        v = _unit([1.0, 0.0, 0.0])
        with patch("embeddings._embed", return_value=v):
            emb.upsert_eng(session, e.id, e.title, e.repo)
        row = session.query(models.EngineeringItemEmbedding).filter_by(item_id=e.id).first()
        assert row is not None
        assert json.loads(row.embedding) == pytest.approx(v)

    def test_upsert_eng_updates_existing_row(self, session):
        e = _eng_item(session, "Fix auth token expiry")
        v1 = _unit([1.0, 0.0])
        v2 = _unit([0.0, 1.0])
        with patch("embeddings._embed", return_value=v1):
            emb.upsert_eng(session, e.id, e.title, e.repo)
        with patch("embeddings._embed", return_value=v2):
            emb.upsert_eng(session, e.id, "Updated title", e.repo)
        rows = session.query(models.EngineeringItemEmbedding).filter_by(item_id=e.id).all()
        assert len(rows) == 1
        assert json.loads(rows[0].embedding) == pytest.approx(v2)

    def test_upsert_eng_no_op_when_embed_fails(self, session):
        e = _eng_item(session, "Fix auth token expiry")
        with patch("embeddings._embed", return_value=None):
            emb.upsert_eng(session, e.id, e.title, e.repo)
        assert session.query(models.EngineeringItemEmbedding).count() == 0

    def test_delete_eng_removes_row(self, session):
        e = _eng_item(session, "Fix auth token expiry")
        with patch("embeddings._embed", return_value=_unit([1.0, 0.0])):
            emb.upsert_eng(session, e.id, e.title, e.repo)
        emb.delete_eng(session, e.id)
        assert session.query(models.EngineeringItemEmbedding).filter_by(item_id=e.id).first() is None

    def test_search_eng_returns_empty_when_embed_fails(self, session):
        e = _eng_item(session, "Fix auth token expiry")
        with patch("embeddings._embed", return_value=None):
            result = emb.search_eng(session, "auth")
        assert result == []

    def test_search_eng_ranks_by_similarity(self, session):
        e_auth    = _eng_item(session, "Fix authentication timeout", repo="owner/repo")
        e_billing = _eng_item(session, "Update invoice PDF generation", repo="owner/repo")

        auth_vec    = _unit([1.0, 0.0])
        billing_vec = _unit([0.0, 1.0])

        def fake_embed(text):
            if "auth" in text.lower() or "Auth" in text:
                return auth_vec
            return billing_vec

        with patch("embeddings._embed", side_effect=fake_embed):
            emb.upsert_eng(session, e_auth.id, e_auth.title, e_auth.repo)
            emb.upsert_eng(session, e_billing.id, e_billing.title, e_billing.repo)

        with patch("embeddings._embed", return_value=auth_vec):
            result = emb.search_eng(session, "auth problem", top_k=2)

        assert result[0] == e_auth.id
        assert result[1] == e_billing.id

    def test_search_eng_top_k_limits_results(self, session):
        items = [_eng_item(session, f"Issue {i}") for i in range(5)]
        v = _unit([1.0, 0.0])
        with patch("embeddings._embed", return_value=v):
            for e in items:
                emb.upsert_eng(session, e.id, e.title, e.repo)
            result = emb.search_eng(session, "query", top_k=2)
        assert len(result) == 2


# ── backfill ──────────────────────────────────────────────────────────────────

class TestBackfill:

    def test_backfill_embeds_unindexed_cards(self, session):
        c1 = _card(session, "Task one")
        c2 = _card(session, "Task two")
        v = _unit([1.0, 0.0])

        with patch("embeddings._embed", return_value=v):
            with patch("database.SessionLocal", Session):
                emb.backfill()

        assert session.query(models.CardEmbedding).count() == 2

    def test_backfill_skips_already_embedded_cards(self, session):
        c1 = _card(session, "Already indexed")
        c2 = _card(session, "Not yet indexed")
        v = _unit([1.0, 0.0])

        # Pre-embed c1
        with patch("embeddings._embed", return_value=v):
            emb.upsert(session, c1.id, c1.title, None)

        call_count = 0

        def counting_embed(text):
            nonlocal call_count
            call_count += 1
            return v

        with patch("embeddings._embed", side_effect=counting_embed):
            with patch("database.SessionLocal", Session):
                emb.backfill()

        # Only c2 should be embedded; c1 was already indexed
        assert session.query(models.CardEmbedding).count() == 2
        assert call_count == 1

    def test_backfill_skips_archived_cards(self, session):
        _card(session, "Active card")
        _card(session, "Archived card", archived=True)
        v = _unit([1.0, 0.0])

        with patch("embeddings._embed", return_value=v):
            with patch("database.SessionLocal", Session):
                emb.backfill()

        assert session.query(models.CardEmbedding).count() == 1

    def test_backfill_embeds_open_engineering_items(self, session):
        e_open   = _eng_item(session, "Open issue",   state="open")
        e_closed = _eng_item(session, "Closed issue", state="closed")
        v = _unit([1.0, 0.0])

        with patch("embeddings._embed", return_value=v):
            with patch("database.SessionLocal", Session):
                emb.backfill()

        assert session.query(models.EngineeringItemEmbedding).count() == 1
        row = session.query(models.EngineeringItemEmbedding).first()
        assert row.item_id == e_open.id

    def test_backfill_skips_already_embedded_engineering_items(self, session):
        e = _eng_item(session, "Already indexed issue")
        v = _unit([1.0, 0.0])

        with patch("embeddings._embed", return_value=v):
            emb.upsert_eng(session, e.id, e.title, e.repo)

        call_count = 0

        def counting_embed(text):
            nonlocal call_count
            call_count += 1
            return v

        with patch("embeddings._embed", side_effect=counting_embed):
            with patch("database.SessionLocal", Session):
                emb.backfill()

        assert session.query(models.EngineeringItemEmbedding).count() == 1
        assert call_count == 0  # nothing new to embed
