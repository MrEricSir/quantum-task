"""
Tests for the assist card-thread endpoints and one-shot/global assist streams:
  GET    /api/cards/{id}/thread
  POST   /api/cards/{id}/thread/message
  PUT    /api/cards/{id}/thread/context
  PUT    /api/cards/{id}/thread/output
  DELETE /api/cards/{id}/thread
  POST   /api/cards/{id}/thread/context-from
  POST   /api/assist/stream
  POST   /api/assist/global

All LLM calls are mocked — no real API calls made. Previously these endpoints
had only incidental coverage from test_search.py/test_telegram.py; this file
gives them dedicated tests.
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from main import app
from deps import get_db


# ── In-memory DB ──────────────────────────────────────────────────────────────

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=engine)
    yield
    models.Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_card(title="Task", section="today", description=None):
    with TestSession() as db:
        c = models.Card(title=title, section=section, description=description, position=0)
        db.add(c)
        db.commit()
        return c.id


def _mock_llm_stream(text="Hello"):
    """Return a mock llm_client() whose chat.completions.create(stream=True) yields one chunk."""
    mock_client = MagicMock()
    chunk = MagicMock()
    chunk.choices[0].delta.content = text
    stream_mock = MagicMock()
    stream_mock.__iter__ = MagicMock(return_value=iter([chunk]))
    mock_client.chat.completions.create.return_value = stream_mock
    return mock_client


def _read_sse(resp):
    """Parse an SSE response body into a list of decoded JSON payloads (skipping [DONE])."""
    events = []
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            continue
        events.append(json.loads(payload))
    return events


# ── GET/PUT/DELETE thread ──────────────────────────────────────────────────────

class TestThreadCrud:

    def test_get_thread_empty_when_none_exists(self, client):
        card_id = _make_card()
        res = client.get(f"/api/cards/{card_id}/thread")
        assert res.status_code == 200
        assert res.json() == {"card_id": card_id, "context": None, "messages": [], "output": None}

    def test_update_context_creates_thread(self, client):
        card_id = _make_card()
        res = client.put(f"/api/cards/{card_id}/thread/context", json={"context": "some doc"})
        assert res.status_code == 200
        assert res.json() == {"ok": True}

        res2 = client.get(f"/api/cards/{card_id}/thread")
        assert res2.json()["context"] == "some doc"

    def test_save_output_creates_thread(self, client):
        card_id = _make_card()
        res = client.put(f"/api/cards/{card_id}/thread/output", json={"output": "draft text"})
        assert res.status_code == 200
        assert res.json() == {"ok": True, "output": "draft text"}

        res2 = client.get(f"/api/cards/{card_id}/thread")
        assert res2.json()["output"] == "draft text"

    def test_clear_thread(self, client):
        card_id = _make_card()
        client.put(f"/api/cards/{card_id}/thread/context", json={"context": "doc"})
        res = client.delete(f"/api/cards/{card_id}/thread")
        assert res.status_code == 200
        assert res.json() == {"ok": True}

        res2 = client.get(f"/api/cards/{card_id}/thread")
        assert res2.json()["context"] is None

    def test_clear_thread_when_none_exists_is_noop(self, client):
        card_id = _make_card()
        res = client.delete(f"/api/cards/{card_id}/thread")
        assert res.status_code == 200
        assert res.json() == {"ok": True}


# ── POST /thread/message ───────────────────────────────────────────────────────

class TestSendMessage:

    def test_404_when_card_not_found(self, client):
        with patch("assist.generate.llm_client", return_value=_mock_llm_stream()):
            res = client.post("/api/cards/9999/thread/message", json={"content": "hi"})
        assert res.status_code == 404

    def test_streams_response_and_persists_messages(self, client):
        card_id = _make_card(title="Fix bug")
        with patch("assist.generate.SessionLocal", TestSession):
            with patch("assist.generate._maybe_web_search", return_value=""):
                with patch("assist.generate.llm_client", return_value=_mock_llm_stream("Sure thing")):
                    res = client.post(f"/api/cards/{card_id}/thread/message", json={"content": "help me"})

        assert res.status_code == 200
        events = _read_sse(res)
        text = "".join(e.get("text", "") for e in events if "text" in e)
        assert text == "Sure thing"

        thread = client.get(f"/api/cards/{card_id}/thread").json()
        msgs = thread["messages"]
        assert msgs[-2] == {"role": "user", "content": "help me", "ts": msgs[-2]["ts"]}
        assert msgs[-1]["role"] == "assistant"
        assert msgs[-1]["content"] == "Sure thing"

    def test_system_prompt_includes_task_title(self, client):
        card_id = _make_card(title="Ship the release")
        mock_llm = _mock_llm_stream()
        with patch("assist.generate._maybe_web_search", return_value=""):
            with patch("assist.generate.llm_client", return_value=mock_llm):
                client.post(f"/api/cards/{card_id}/thread/message", json={"content": "status?"})

        call_args = mock_llm.chat.completions.create.call_args
        system_msg = call_args[1]["messages"][0]["content"]
        assert "Ship the release" in system_msg

    def test_web_search_results_injected(self, client):
        card_id = _make_card()
        mock_llm = _mock_llm_stream()
        with patch("assist.generate._maybe_web_search", return_value="[Result](http://x)\nsome content"):
            with patch("assist.generate.llm_client", return_value=mock_llm):
                res = client.post(f"/api/cards/{card_id}/thread/message", json={"content": "find a hotel"})

        events = _read_sse(res)
        assert any(e.get("status") == "searching" for e in events)
        call_args = mock_llm.chat.completions.create.call_args
        user_msg = call_args[1]["messages"][-1]["content"]
        assert "Web Search Results" in user_msg

    def test_llm_error_yields_error_event(self, client):
        card_id = _make_card()
        mock_llm = MagicMock()
        mock_llm.chat.completions.create.side_effect = Exception("boom")
        with patch("assist.generate._maybe_web_search", return_value=""):
            with patch("assist.generate.llm_client", return_value=mock_llm):
                res = client.post(f"/api/cards/{card_id}/thread/message", json={"content": "hi"})

        events = _read_sse(res)
        assert any("error" in e for e in events)


# ── POST /thread/context-from ──────────────────────────────────────────────────

class TestContextFrom:

    def test_404_when_card_not_found(self, client):
        res = client.post("/api/cards/9999/thread/context-from", json={"source": "section"})
        assert res.status_code == 404

    def test_section_source(self, client):
        card_id = _make_card(title="Main card")
        _make_card(title="Other today task", section="today", description="details here")

        res = client.post(f"/api/cards/{card_id}/thread/context-from", json={"source": "section", "section": "today"})
        assert res.status_code == 200
        body = res.json()
        assert body["label"] == "Today"
        assert body["count"] == 1
        assert "Other today task" in body["context_text"]

    def test_section_excludes_self(self, client):
        card_id = _make_card(title="Main card", section="today")
        res = client.post(f"/api/cards/{card_id}/thread/context-from", json={"source": "section", "section": "today"})
        assert res.json()["count"] == 0

    def test_tag_source_requires_tag_id(self, client):
        card_id = _make_card()
        res = client.post(f"/api/cards/{card_id}/thread/context-from", json={"source": "tag"})
        assert res.status_code == 400

    def test_tag_source_unknown_tag_returns_empty(self, client):
        card_id = _make_card()
        res = client.post(f"/api/cards/{card_id}/thread/context-from", json={"source": "tag", "tag_id": 9999})
        assert res.status_code == 200
        assert res.json()["count"] == 0

    def test_similar_source_uses_embeddings(self, client):
        card_id = _make_card(title="Main card")
        other_id = _make_card(title="Related card")

        with patch("embeddings.search", return_value=[other_id, card_id]):
            res = client.post(f"/api/cards/{card_id}/thread/context-from", json={"source": "similar"})

        assert res.status_code == 200
        body = res.json()
        assert body["count"] == 1
        assert "Related card" in body["context_text"]

    def test_similar_source_skips_gracefully_on_error(self, client):
        card_id = _make_card()
        with patch("embeddings.search", side_effect=Exception("embed down")):
            res = client.post(f"/api/cards/{card_id}/thread/context-from", json={"source": "similar"})
        assert res.status_code == 200
        assert res.json()["count"] == 0


# ── POST /api/assist/stream ─────────────────────────────────────────────────────

class TestStreamAssist:

    def test_streams_llm_response(self, client):
        with patch("assist.generate._maybe_web_search", return_value=""):
            with patch("assist.generate.llm_client", return_value=_mock_llm_stream("An answer")):
                res = client.post("/api/assist/stream", json={
                    "card_title": "Plan trip",
                    "context": "Need hotel options",
                })

        assert res.status_code == 200
        events = _read_sse(res)
        text = "".join(e.get("text", "") for e in events if "text" in e)
        assert text == "An answer"

    def test_includes_card_description_in_prompt(self, client):
        mock_llm = _mock_llm_stream()
        with patch("assist.generate._maybe_web_search", return_value=""):
            with patch("assist.generate.llm_client", return_value=mock_llm):
                client.post("/api/assist/stream", json={
                    "card_title": "Plan trip",
                    "card_description": "Anniversary weekend",
                    "context": "Need hotel options",
                })

        call_args = mock_llm.chat.completions.create.call_args
        user_msg = call_args[1]["messages"][-1]["content"]
        assert "Anniversary weekend" in user_msg

    def test_reverse_geocodes_when_coords_given(self, client):
        mock_llm = _mock_llm_stream()
        with patch("assist.generate._reverse_geocode", return_value="Austin, TX"):
            with patch("assist.generate._maybe_web_search", return_value=""):
                with patch("assist.generate.llm_client", return_value=mock_llm):
                    client.post("/api/assist/stream", json={
                        "card_title": "Find lunch",
                        "context": "nearby",
                        "lat": 30.27,
                        "lon": -97.74,
                    })

        call_args = mock_llm.chat.completions.create.call_args
        user_msg = call_args[1]["messages"][-1]["content"]
        assert "Austin, TX" in user_msg


# ── POST /api/assist/global ─────────────────────────────────────────────────────

class TestGlobalAssist:

    def test_streams_llm_response(self, client):
        with patch("assist.generate._maybe_web_search", return_value=""):
            with patch("assist.generate.llm_client", return_value=_mock_llm_stream("Global answer")):
                res = client.post("/api/assist/global", json={"prompt": "what's next"})

        assert res.status_code == 200
        events = _read_sse(res)
        text = "".join(e.get("text", "") for e in events if "text" in e)
        assert text == "Global answer"

    def test_section_filter_injects_matching_cards(self, client):
        _make_card(title="Weekly review", section="week")
        mock_llm = _mock_llm_stream()
        with patch("assist.generate._maybe_web_search", return_value=""):
            with patch("assist.generate.SessionLocal", TestSession):
                with patch("assist.generate.llm_client", return_value=mock_llm):
                    client.post("/api/assist/global", json={"prompt": "summarize", "section": "week"})

        call_args = mock_llm.chat.completions.create.call_args
        user_msg = call_args[1]["messages"][-1]["content"]
        assert "Weekly review" in user_msg

    def test_llm_error_yields_error_event(self, client):
        mock_llm = MagicMock()
        mock_llm.chat.completions.create.side_effect = Exception("boom")
        with patch("assist.generate._maybe_web_search", return_value=""):
            with patch("assist.generate.llm_client", return_value=mock_llm):
                res = client.post("/api/assist/global", json={"prompt": "hi"})

        events = _read_sse(res)
        assert any("error" in e for e in events)


# ── _maybe_web_search internals ──────────────────────────────────────────────
# Every other test in this file mocks _maybe_web_search as a black box -- these test its
# own LLM call and error handling directly, previously with zero coverage anywhere.

class TestMaybeWebSearch:

    def _mock_decision(self, content):
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=content))]
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        return client

    def test_returns_empty_string_when_tavily_not_configured(self):
        from assist.context import _maybe_web_search
        with patch("assist.context._ASSIST_TAVILY_KEY", ""):
            assert _maybe_web_search("find a hotel in Austin") == ""

    def test_requests_reasoning_effort_low_and_json_mode(self):
        """The reliability fix: on a reasoning model, an unbounded chain-of-thought can burn
        the whole max_tokens budget before the real JSON decision, truncating it -- see
        correlations.py's _generate_experiment for the full story."""
        from assist.context import _maybe_web_search
        mock_client = self._mock_decision('{"search": false}')
        with patch("assist.context._ASSIST_TAVILY_KEY", "fake-key"), \
             patch("assist.context.llm_client", return_value=mock_client):
            _maybe_web_search("what's the weather like")

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "low"
        assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_no_search_needed_returns_empty_string(self):
        from assist.context import _maybe_web_search
        mock_client = self._mock_decision('{"search": false}')
        with patch("assist.context._ASSIST_TAVILY_KEY", "fake-key"), \
             patch("assist.context.llm_client", return_value=mock_client):
            assert _maybe_web_search("what's on my todo list") == ""

    def test_search_needed_runs_tavily_and_formats_results(self):
        from assist.context import _maybe_web_search
        mock_client = self._mock_decision('{"search": true, "queries": ["sushi austin"]}')
        fake_results = [{"title": "Best Sushi", "url": "http://x.com", "content": "Great rolls"}]
        with patch("assist.context._ASSIST_TAVILY_KEY", "fake-key"), \
             patch("assist.context.llm_client", return_value=mock_client), \
             patch("assist.context._tavily_search", return_value=fake_results):
            result = _maybe_web_search("best sushi in austin")

        assert "Best Sushi" in result
        assert "Great rolls" in result

    def test_truncated_json_is_logged_not_silently_swallowed(self, capsys):
        """Previously a bare `except Exception: pass` -- a truncated/malformed decision
        response (exactly what an unbounded reasoning trace can cause) failed with zero
        trace anywhere that web search silently never triggers."""
        from assist.context import _maybe_web_search
        mock_client = self._mock_decision('{"search": true, "queri')  # truncated mid-JSON
        with patch("assist.context._ASSIST_TAVILY_KEY", "fake-key"), \
             patch("assist.context.llm_client", return_value=mock_client):
            result = _maybe_web_search("find a hotel")

        assert result == ""
        assert "web search decision error" in capsys.readouterr().out

    def test_llm_exception_returns_empty_string_and_is_logged(self, capsys):
        from assist.context import _maybe_web_search
        broken_client = MagicMock()
        broken_client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch("assist.context._ASSIST_TAVILY_KEY", "fake-key"), \
             patch("assist.context.llm_client", return_value=broken_client):
            result = _maybe_web_search("find a hotel")

        assert result == ""
        assert "boom" in capsys.readouterr().out
