"""
Tests for routers/cards.py's parse_bulk_text() -- the shared LLM-call +
split + resolve_dates + post_process pipeline behind POST /api/cards/parse-bulk
(webapp's Quick Add) and telegram/bot.py's _capture_from_text (Telegram's
capture fallback). Had no dedicated test coverage before this file; the LLM
call is mocked throughout, so these exercise the surrounding glue --
splitting into multiple ParsedCard items and the two DB-creation helpers
(create_card_row, create_habit_row) extracted alongside it -- not the LLM's
own classification quality.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from main import app
from deps import get_db

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=test_engine)
    yield
    models.Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _fake_llm_client(json_text):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json_text))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class TestParseBulkText:

    def test_splits_multiple_items(self):
        from routers.cards import parse_bulk_text
        fake = _fake_llm_client(
            '{"items": ['
            '{"type": "task", "title": "call dentist", "section": "today"},'
            '{"type": "task", "title": "buy eggs", "section": "later"}'
            ']}'
        )
        with TestingSessionLocal() as db, patch("routers.cards.llm_client", return_value=fake):
            items = parse_bulk_text(db, "call dentist, buy eggs", date(2026, 8, 16))
        assert [i.title for i in items] == ["call dentist", "buy eggs"]

    def test_single_item_still_returns_a_list(self):
        from routers.cards import parse_bulk_text
        fake = _fake_llm_client('{"items": [{"type": "task", "title": "buy milk", "section": "today"}]}')
        with TestingSessionLocal() as db, patch("routers.cards.llm_client", return_value=fake):
            items = parse_bulk_text(db, "buy milk", date(2026, 8, 16))
        assert len(items) == 1
        assert items[0].title == "buy milk"

    def test_empty_items_returns_empty_list(self):
        from routers.cards import parse_bulk_text
        fake = _fake_llm_client('{"items": []}')
        with TestingSessionLocal() as db, patch("routers.cards.llm_client", return_value=fake):
            items = parse_bulk_text(db, "   ", date(2026, 8, 16))
        assert items == []


class TestParseBulkEndpoint:

    def test_returns_items_from_parse_bulk_text(self, client):
        import schemas
        fake_items = [schemas.ParsedCard(type="task", title="buy milk", section="today")]
        with patch("routers.cards.parse_bulk_text", return_value=fake_items):
            r = client.post("/api/cards/parse-bulk", json={"text": "buy milk"})
        assert r.status_code == 200
        assert r.json()["items"][0]["title"] == "buy milk"

    def test_returns_503_on_llm_failure(self, client):
        with patch("routers.cards.parse_bulk_text", side_effect=RuntimeError("LLM down")):
            r = client.post("/api/cards/parse-bulk", json={"text": "buy milk"})
        assert r.status_code == 503


class TestCreateCardRow:

    def test_creates_a_card_with_tags(self):
        from routers.cards import create_card_row
        with TestingSessionLocal() as db:
            tag = models.Tag(name="Errands")
            db.add(tag)
            db.commit()
            card = create_card_row(db, {"title": "buy milk", "section": "today"}, [tag.id])
            assert card.title == "buy milk"
            assert card.section == "today"
            assert {t.name for t in card.tags} == {"Errands"}

    def test_sets_today_since_when_section_is_today(self):
        from routers.cards import create_card_row
        with TestingSessionLocal() as db:
            card = create_card_row(db, {"title": "buy milk", "section": "today"}, [])
            assert card.today_since is not None

    def test_position_increments_within_section(self):
        from routers.cards import create_card_row
        with TestingSessionLocal() as db:
            create_card_row(db, {"title": "first", "section": "later"}, [])
            second = create_card_row(db, {"title": "second", "section": "later"}, [])
            assert second.position == 1


class TestCreateHabitRow:

    def test_creates_a_plain_habit(self):
        from routers.habits import create_habit_row
        with TestingSessionLocal() as db:
            habit = create_habit_row(db, "Meditate")
            assert habit.name == "Meditate"
            assert habit.health_metric is None

    def test_creates_a_health_metric_habit_with_tags(self):
        from routers.habits import create_habit_row
        with TestingSessionLocal() as db:
            tag = models.Tag(name="Health")
            db.add(tag)
            db.commit()
            habit = create_habit_row(db, "Daily Steps", "steps", 10000.0, [tag.id])
            assert habit.health_metric == "steps"
            assert habit.health_goal == 10000.0
            assert {t.name for t in habit.tags} == {"Health"}
