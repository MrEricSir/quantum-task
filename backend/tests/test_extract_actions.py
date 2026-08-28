"""
Tests for action_items.extract_action_items() and POST
/api/cards/{id}/extract-actions -- the LLM call is mocked throughout (see
tests/test_parse.py for the separate real-Ollama prompt-quality tests), so
these exercise the surrounding glue: request validation, error handling, and
that extracted items flow through resolve_dates the same way Quick Add's own
parsed items do.
"""
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


class TestExtractActionItems:

    def test_returns_parsed_cards(self):
        from action_items import extract_action_items
        fake = _fake_llm_client(
            '{"items": [{"title": "Follow up with design", "description": null, '
            '"scheduled_at": null, "suggested_tags": []}]}'
        )
        with TestingSessionLocal() as db, patch("action_items.llm_client", return_value=fake):
            items = extract_action_items(db, "Sarah will follow up with design.", date(2026, 8, 16))
        assert len(items) == 1
        assert items[0].title == "Follow up with design"
        assert items[0].type == "task"

    def test_no_action_items_returns_empty_list(self):
        from action_items import extract_action_items
        fake = _fake_llm_client('{"items": []}')
        with TestingSessionLocal() as db, patch("action_items.llm_client", return_value=fake):
            items = extract_action_items(db, "We discussed the Q3 roadmap.", date(2026, 8, 16))
        assert items == []

    def test_malformed_item_is_skipped_not_fatal(self):
        from action_items import extract_action_items
        # First item is missing the required "title" field -- should be
        # dropped, not raise and lose the second, valid item.
        fake = _fake_llm_client(
            '{"items": [{"description": "no title here"}, {"title": "Ship the fix"}]}'
        )
        with TestingSessionLocal() as db, patch("action_items.llm_client", return_value=fake):
            items = extract_action_items(db, "some text", date(2026, 8, 16))
        assert [i.title for i in items] == ["Ship the fix"]

    def test_relative_date_phrase_in_title_is_resolved(self):
        # Same resolve_dates() integration parse_bulk_text relies on -- a
        # "tomorrow"/weekday phrase in the item's own title overrides
        # whatever (or no) scheduled_at the LLM produced.
        from action_items import extract_action_items
        fake = _fake_llm_client('{"items": [{"title": "Submit report tomorrow", "scheduled_at": null}]}')
        with TestingSessionLocal() as db, patch("action_items.llm_client", return_value=fake):
            items = extract_action_items(db, "Submit report tomorrow.", date(2026, 8, 16))
        assert items[0].scheduled_at is not None
        assert items[0].scheduled_at.date() == date(2026, 8, 17)

    def test_date_phrase_stripped_from_title_is_still_resolved_via_source_text(self):
        # Regression test: the prompt instructs the LLM to strip date phrases out of
        # the title (see the module's own worked examples), so resolve_dates must be
        # given source_text -- not the sanitized title -- or it silently never fires.
        from action_items import extract_action_items
        fake = _fake_llm_client(
            '{"items": [{"title": "Submit report", "source_text": '
            '"submit report tomorrow", "scheduled_at": null}]}'
        )
        with TestingSessionLocal() as db, patch("action_items.llm_client", return_value=fake):
            items = extract_action_items(db, "Please submit report tomorrow.", date(2026, 8, 16))
        assert items[0].title == "Submit report"
        assert items[0].scheduled_at is not None
        assert items[0].scheduled_at.date() == date(2026, 8, 17)


class TestExtractActionsEndpoint:

    def test_card_not_found_returns_404(self, client):
        r = client.post("/api/cards/999/extract-actions")
        assert r.status_code == 404

    def test_empty_description_returns_400(self, client):
        with TestingSessionLocal() as db:
            card = models.Card(title="Notes", description="   ", section="later")
            db.add(card)
            db.commit()
            card_id = card.id
        r = client.post(f"/api/cards/{card_id}/extract-actions")
        assert r.status_code == 400

    def test_returns_extracted_items(self, client):
        import schemas
        with TestingSessionLocal() as db:
            card = models.Card(title="Notes", description="Sarah will follow up.", section="later")
            db.add(card)
            db.commit()
            card_id = card.id
        fake_items = [schemas.ParsedCard(type="task", title="Follow up with design")]
        with patch("routers.cards.extract_action_items", return_value=fake_items):
            r = client.post(f"/api/cards/{card_id}/extract-actions")
        assert r.status_code == 200
        assert r.json()["items"][0]["title"] == "Follow up with design"

    def test_returns_503_on_llm_failure(self, client):
        with TestingSessionLocal() as db:
            card = models.Card(title="Notes", description="Sarah will follow up.", section="later")
            db.add(card)
            db.commit()
            card_id = card.id
        with patch("routers.cards.extract_action_items", side_effect=RuntimeError("LLM down")):
            r = client.post(f"/api/cards/{card_id}/extract-actions")
        assert r.status_code == 503
