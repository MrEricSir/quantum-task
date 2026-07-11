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


def post_briefing(client):
    return client.post("/api/briefing/stream", json={
        "force": True,   # bypass cache so every call hits the generation path
        "today_only": True,
    })


def create_card(client, title="Test card", section="today"):
    r = client.post("/api/cards", json={"title": title, "section": section})
    return r.json()["id"]


def create_habit(client, name):
    r = client.post("/api/habits", json={"name": name})
    return r.json()["id"]


def complete_habit(client, habit_id):
    client.post(f"/api/habits/{habit_id}/check")


def today_text(events: list[dict]) -> str | None:
    return next((e["text"] for e in events if e.get("section") == "today"), None)


# ── Tests ────────────────────────────────────────────────────────────────────

class TestBriefingHallucinationGuard:
    def test_no_content_skips_llm(self, client):
        """Empty DB (no todos, no habits) → static message without calling the LLM."""
        with patch("routers.briefing.llm_client") as mock_llm:
            res = post_briefing(client)

        assert res.status_code == 200
        mock_llm.assert_not_called()
        assert today_text(parse_sse(res.text)) == "Nothing scheduled today."

    def test_all_habits_completed_skips_llm(self, client):
        """All habits done + no todos/events → static message, LLM not called."""
        h1 = create_habit(client, "Meditate")
        h2 = create_habit(client, "Exercise")
        complete_habit(client, h1)
        complete_habit(client, h2)

        with patch("routers.briefing.llm_client") as mock_llm:
            res = post_briefing(client)

        assert res.status_code == 200
        mock_llm.assert_not_called()
        assert today_text(parse_sse(res.text)) == "Nothing scheduled today."

    def test_pending_habit_calls_llm(self, client):
        """A pending habit → LLM is called to generate the briefing."""
        create_habit(client, "Meditate")          # pending
        h2 = create_habit(client, "Exercise")
        complete_habit(client, h2)               # completed

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([])
        with patch("routers.briefing.llm_client", return_value=mock_client):
            res = post_briefing(client)

        assert res.status_code == 200
        mock_client.chat.completions.create.assert_called_once()

    def test_today_card_calls_llm(self, client):
        """A card in the 'today' section → LLM is called (not the static no-content path)."""
        create_card(client, title="Write tests", section="today")

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([])
        with patch("routers.briefing.llm_client", return_value=mock_client):
            res = post_briefing(client)

        assert res.status_code == 200
        mock_client.chat.completions.create.assert_called_once()

    def test_mixed_habits_and_completed_only_shows_pending(self, client):
        """Only pending habits appear in the LLM context; completed ones are excluded."""
        create_habit(client, "Meditate")          # pending
        h2 = create_habit(client, "Exercise")
        complete_habit(client, h2)               # completed

        captured_context = {}
        mock_client = MagicMock()

        def capture(*args, **kwargs):
            captured_context["messages"] = kwargs.get("messages", [])
            return iter([])

        mock_client.chat.completions.create.side_effect = capture
        with patch("routers.briefing.llm_client", return_value=mock_client):
            post_briefing(client)

        user_msg = next(m["content"] for m in captured_context["messages"] if m["role"] == "user")
        assert "Meditate" in user_msg
        assert "Exercise" not in user_msg
