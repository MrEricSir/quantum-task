"""
Unit tests for the daily briefing endpoint.

Verifies that the briefing skips the LLM and returns a static
"Nothing scheduled today." message when there is nothing actionable,
preventing hallucination when the LLM receives an empty context.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from main import app
from deps import get_db

# ── In-memory test database ──────────────────────────────────────────────────

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


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_sse(text: str) -> list[dict]:
    """Parse SSE response body into a list of data payloads (excludes [DONE])."""
    results = []
    for line in text.splitlines():
        if line.startswith("data: "):
            data = line[6:].strip()
            if data != "[DONE]":
                results.append(json.loads(data))
    return results


def post_briefing(client, habits=None, cards=None, calendar_events=None):
    return client.post("/api/briefing/stream", json={
        "cards": cards or [],
        "calendar_events": calendar_events or [],
        "habits": habits or [],
        "force": True,   # bypass cache so every call hits the generation path
        "today_only": True,
    })


def today_text(events: list[dict]) -> str | None:
    return next((e["text"] for e in events if e.get("section") == "today"), None)


# ── Tests ────────────────────────────────────────────────────────────────────

class TestBriefingHallucinationGuard:
    def test_no_content_skips_llm(self, client):
        """No todos, no events, no habits → static message without calling the LLM."""
        with patch("routers.briefing.llm_client") as mock_llm:
            res = post_briefing(client)

        assert res.status_code == 200
        mock_llm.assert_not_called()
        assert today_text(parse_sse(res.text)) == "Nothing scheduled today."

    def test_all_habits_completed_skips_llm(self, client):
        """All habits done + no todos/events → static message, LLM not called."""
        habits = [
            {"name": "Meditate", "completed_today": True},
            {"name": "Exercise", "completed_today": True},
        ]
        with patch("routers.briefing.llm_client") as mock_llm:
            res = post_briefing(client, habits=habits)

        assert res.status_code == 200
        mock_llm.assert_not_called()
        assert today_text(parse_sse(res.text)) == "Nothing scheduled today."

    def test_pending_habit_calls_llm(self, client):
        """A pending habit → LLM is called to generate the briefing."""
        habits = [
            {"name": "Meditate", "completed_today": False},
            {"name": "Exercise", "completed_today": True},
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([])
        with patch("routers.briefing.llm_client", return_value=mock_client):
            res = post_briefing(client, habits=habits)

        assert res.status_code == 200
        mock_client.chat.completions.create.assert_called_once()

    def test_today_card_calls_llm(self, client):
        """A card in the 'today' section → LLM is called (not the static no-content path)."""
        cards = [{"id": 1, "title": "Write tests", "section": "today",
                  "description": None, "body": None, "scheduled_at": None,
                  "completed": False, "completed_at": None, "position": 0,
                  "created_at": "2026-01-01T00:00:00", "updated_at": None,
                  "archived": False, "archived_at": None, "raw_input": None,
                  "recurrence_rule": None, "external_id": None,
                  "snoozed_until": None, "waiting_reason": None, "tags": []}]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([])
        with patch("routers.briefing.llm_client", return_value=mock_client):
            res = post_briefing(client, cards=cards)

        assert res.status_code == 200
        mock_client.chat.completions.create.assert_called_once()

    def test_mixed_habits_and_completed_only_shows_pending(self, client):
        """Only pending habits appear in the LLM context; completed ones are excluded."""
        habits = [
            {"name": "Meditate", "completed_today": False},
            {"name": "Exercise", "completed_today": True},
        ]
        captured_context = {}
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([])

        def capture(*args, **kwargs):
            captured_context["messages"] = kwargs.get("messages", [])
            return iter([])

        mock_client.chat.completions.create.side_effect = capture
        with patch("routers.briefing.llm_client", return_value=mock_client):
            post_briefing(client, habits=habits)

        user_msg = next(m["content"] for m in captured_context["messages"] if m["role"] == "user")
        assert "Meditate" in user_msg
        assert "Exercise" not in user_msg
