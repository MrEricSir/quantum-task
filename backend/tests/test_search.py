"""
Tests for semantic search integration across:
  - telegram/bot.py _reply_search_cards — covers cards + GitHub items
  - routers/assist.py global_assist — injects semantic context when no filter
  - github_sync.py — calls upsert_eng_bg after sync
"""
import json
import math
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, call

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from main import app
from deps import get_db


# ── DB fixtures ───────────────────────────────────────────────────────────────

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=engine)


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


@pytest.fixture
def session():
    s = TestSession()
    yield s
    s.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _unit(v):
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v]


def _make_card(title, section="today", description=None, completed=False, archived=False):
    with TestSession() as db:
        c = models.Card(title=title, section=section, description=description,
                        completed=completed, archived=archived, position=0)
        db.add(c)
        db.commit()
        return c.id


def _make_eng_item(title, repo="owner/repo", state="open", project_status=None):
    with TestSession() as db:
        e = models.EngineeringItem(
            external_id=f"github:{repo}/{title}",
            title=title, item_type="issue", repo=repo,
            number=1, url=f"https://github.com/{repo}/issues/1",
            state=state, project_status=project_status,
            synced_at=datetime.now(timezone.utc),
        )
        db.add(e)
        db.commit()
        return e.id


# ── _reply_search_cards ───────────────────────────────────────────────────────

class TestReplySearchCards:

    def test_missing_query_returns_prompt(self):
        from telegram.bot import _reply_search_cards
        with patch("telegram.bot.SessionLocal", TestSession):
            reply = _reply_search_cards({})
        assert "search" in reply.lower() or "?" in reply

    def test_finds_card_by_semantic_search(self):
        card_id = _make_card("Deploy the authentication service")
        from telegram.bot import _reply_search_cards

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[card_id]):
                with patch("embeddings.search_eng", return_value=[]):
                    reply = _reply_search_cards({"query": "auth deployment"})

        assert "Deploy the authentication service" in reply

    def test_finds_engineering_item_by_semantic_search(self):
        eng_id = _make_eng_item("Fix OAuth token refresh", repo="org/backend")
        from telegram.bot import _reply_search_cards

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[]):
                with patch("embeddings.search_eng", return_value=[eng_id]):
                    reply = _reply_search_cards({"query": "oauth token"})

        assert "Fix OAuth token refresh" in reply
        assert "GitHub" in reply

    def test_shows_both_cards_and_engineering_items(self):
        card_id = _make_card("Write auth docs")
        eng_id  = _make_eng_item("Auth service refactor")
        from telegram.bot import _reply_search_cards

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[card_id]):
                with patch("embeddings.search_eng", return_value=[eng_id]):
                    reply = _reply_search_cards({"query": "auth"})

        assert "Write auth docs" in reply
        assert "Auth service refactor" in reply
        assert "Tasks" in reply or "Notes" in reply
        assert "GitHub" in reply

    def test_falls_back_to_substring_for_cards_when_no_embeddings(self):
        _make_card("Deploy authentication module")
        from telegram.bot import _reply_search_cards

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[]):
                with patch("embeddings.search_eng", return_value=[]):
                    reply = _reply_search_cards({"query": "authentication"})

        assert "Deploy authentication module" in reply

    def test_falls_back_to_substring_for_eng_items_when_no_embeddings(self):
        _make_eng_item("Fix authentication bug")
        from telegram.bot import _reply_search_cards

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[]):
                with patch("embeddings.search_eng", return_value=[]):
                    reply = _reply_search_cards({"query": "authentication"})

        assert "Fix authentication bug" in reply

    def test_returns_no_results_message_when_nothing_matches(self):
        from telegram.bot import _reply_search_cards

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[]):
                with patch("embeddings.search_eng", return_value=[]):
                    reply = _reply_search_cards({"query": "xyzzy nonexistent"})

        assert "No results" in reply or "no results" in reply.lower()

    def test_completed_card_shows_checkmark(self):
        card_id = _make_card("Finished task", completed=True)
        from telegram.bot import _reply_search_cards

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[card_id]):
                with patch("embeddings.search_eng", return_value=[]):
                    reply = _reply_search_cards({"query": "finished"})

        assert "✅" in reply

    def test_engineering_item_shows_project_status(self):
        eng_id = _make_eng_item("API rate limiting", project_status="In Progress")
        from telegram.bot import _reply_search_cards

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[]):
                with patch("embeddings.search_eng", return_value=[eng_id]):
                    reply = _reply_search_cards({"query": "api rate"})

        assert "In Progress" in reply

    def test_archived_cards_excluded_from_results(self):
        archived_id = _make_card("Archived task", archived=True)
        active_id   = _make_card("Active task")
        from telegram.bot import _reply_search_cards

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[archived_id, active_id]):
                with patch("embeddings.search_eng", return_value=[]):
                    reply = _reply_search_cards({"query": "task"})

        assert "Active task" in reply
        assert "Archived task" not in reply

    def test_sets_last_card_in_session(self):
        card_id = _make_card("My task")
        from telegram.bot import _reply_search_cards, _sessions
        chat_id = "test_session_card"
        _sessions.pop(chat_id, None)

        with patch("telegram.bot.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[card_id]):
                with patch("embeddings.search_eng", return_value=[]):
                    _reply_search_cards({"query": "task"}, chat_id=chat_id)

        assert _sessions[chat_id]["last_card"]["id"] == card_id


# ── global_assist semantic injection ──────────────────────────────────────────

class TestGlobalAssistSemanticInjection:
    """When no section/tag filter is set, global_assist injects semantically relevant context."""

    def _stream_assist(self, client, prompt, **kwargs):
        payload = {"prompt": prompt, **kwargs}
        with patch("routers.assist._maybe_web_search", return_value=""):
            with patch("routers.assist.llm_client") as mock_llm:
                stream_mock = MagicMock()
                chunk = MagicMock()
                chunk.choices[0].delta.content = "OK"
                stream_mock.__iter__ = MagicMock(return_value=iter([chunk]))
                mock_llm.return_value.chat.completions.create.return_value = stream_mock
                resp = client.post("/api/assist/global", json=payload)
        return mock_llm, resp

    def test_injects_relevant_cards_when_no_filter(self, client):
        card_id = _make_card("Deploy authentication service", description="JWT refresh flow")

        with patch("routers.assist.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[card_id]):
                with patch("embeddings.search_eng", return_value=[]):
                    mock_llm, resp = self._stream_assist(client, "tell me about auth")

        assert resp.status_code == 200
        call_args = mock_llm.return_value.chat.completions.create.call_args
        messages = call_args[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "Deploy authentication service" in user_content
        assert "relevant tasks" in user_content.lower()

    def test_injects_relevant_engineering_items_when_no_filter(self, client):
        eng_id = _make_eng_item("Fix OAuth flow", repo="org/api", project_status="In Progress")

        with patch("routers.assist.SessionLocal", TestSession):
            with patch("embeddings.search", return_value=[]):
                with patch("embeddings.search_eng", return_value=[eng_id]):
                    mock_llm, resp = self._stream_assist(client, "oauth issues")

        assert resp.status_code == 200
        call_args = mock_llm.return_value.chat.completions.create.call_args
        messages = call_args[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "Fix OAuth flow" in user_content
        assert "GitHub" in user_content

    def test_no_semantic_injection_when_section_filter_set(self, client):
        card_id = _make_card("Deploy authentication service")

        search_mock = MagicMock(return_value=[card_id])
        with patch("embeddings.search", search_mock):
            with patch("embeddings.search_eng", return_value=[]):
                mock_llm, resp = self._stream_assist(client, "auth", section="today")

        # search() should NOT be called for semantic injection when section is set
        search_mock.assert_not_called()

    def test_semantic_injection_skips_gracefully_on_error(self, client):
        """If embedding service errors, the assistant still responds."""
        with patch("embeddings.search", side_effect=Exception("embed down")):
            mock_llm, resp = self._stream_assist(client, "some query")

        assert resp.status_code == 200

    def test_no_relevant_cards_section_omitted(self, client):
        """If no cards match semantically, the 'relevant tasks' section is not injected."""
        with patch("embeddings.search", return_value=[]):
            with patch("embeddings.search_eng", return_value=[]):
                mock_llm, resp = self._stream_assist(client, "random query")

        call_args = mock_llm.return_value.chat.completions.create.call_args
        messages = call_args[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "relevant tasks" not in user_content.lower()
        assert "relevant github" not in user_content.lower()


# ── github_sync embedding hook ────────────────────────────────────────────────

class TestGithubSyncEmbedding:
    """github_sync.sync() should call upsert_eng_bg for open items after sync."""

    def _mock_fetch_items(self, items):
        """Build a fake _fetch_items return value."""
        return [
            {
                "title": it["title"],
                "number": i + 1,
                "html_url": f"https://github.com/{it['repo']}/issues/{i + 1}",
                "pull_request": None,
            }
            for i, it in enumerate(items)
        ]

    def test_upsert_eng_bg_called_for_open_items(self):
        """After a successful sync, upsert_eng_bg is called for every open item."""
        from github_sync import sync
        import app_setting_keys as sk

        with TestSession() as db:
            db.add(models.AppSetting(key=sk.GITHUB_TOKEN, value="token"))
            db.add(models.AppSetting(key=sk.GITHUB_REPOS, value="owner/repo"))
            db.commit()

        fetch_items = self._mock_fetch_items([
            {"title": "Fix login bug", "repo": "owner/repo"},
            {"title": "Add dark mode",  "repo": "owner/repo"},
        ])

        with TestSession() as db:
            with patch("github_sync._fetch_items", return_value=fetch_items):
                with patch("github_sync._fetch_project_statuses", return_value={}):
                    with patch("github_sync.get_status_config", return_value={}):
                        with patch("embeddings.upsert_eng_bg") as mock_upsert:
                            result = sync(db)

        assert result["error"] is None
        assert mock_upsert.call_count == 2
        called_titles = {c.args[1] for c in mock_upsert.call_args_list}
        assert "Fix login bug" in called_titles
        assert "Add dark mode" in called_titles

    def test_sync_continues_when_embedding_fails(self):
        """If embedding raises an exception, sync still completes successfully."""
        from github_sync import sync
        import app_setting_keys as sk

        with TestSession() as db:
            db.add(models.AppSetting(key=sk.GITHUB_TOKEN, value="token"))
            db.add(models.AppSetting(key=sk.GITHUB_REPOS, value="owner/repo"))
            db.commit()

        fetch_items = self._mock_fetch_items([{"title": "Some issue", "repo": "owner/repo"}])

        with TestSession() as db:
            with patch("github_sync._fetch_items", return_value=fetch_items):
                with patch("github_sync._fetch_project_statuses", return_value={}):
                    with patch("github_sync.get_status_config", return_value={}):
                        with patch("embeddings.upsert_eng_bg", side_effect=Exception("embed down")):
                            result = sync(db)

        assert result["error"] is None


# ── Calendar context injection ─────────────────────────────────────────────────

class TestCalendarContextInjection:
    """Calendar events are injected into both the card thread and global assist."""

    TOMORROW = date.today() + timedelta(days=1)

    def _make_mapping(self):
        """Create a CalendarMapping in the test DB (SQLite won't enforce the tag FK)."""
        with TestSession() as db:
            db.add(models.CalendarMapping(tag_id=1, ical_url="https://example.com/cal.ics", name="Work"))
            db.commit()

    def _fake_events(self):
        tomorrow = self.TOMORROW
        return [{
            "id": "ev1", "uid": "ev1@test", "sequence": 0,
            "title": "Team standup", "description": None, "location": None, "url": None,
            "start": datetime(tomorrow.year, tomorrow.month, tomorrow.day, 9, 0, tzinfo=timezone.utc),
            "end": None, "all_day": False, "is_ooo": False,
            "local_date": tomorrow, "time_str": "9:00 AM",
        }]

    def _stream_thread(self, client, card_id, message):
        with patch("routers.assist._maybe_web_search", return_value=""):
            with patch("routers.assist.llm_client") as mock_llm:
                stream_mock = MagicMock()
                chunk = MagicMock()
                chunk.choices[0].delta.content = "Team standup at 9 AM"
                stream_mock.__iter__ = MagicMock(return_value=iter([chunk]))
                mock_llm.return_value.chat.completions.create.return_value = stream_mock
                resp = client.post(
                    f"/api/cards/{card_id}/thread/message",
                    json={"content": message},
                    headers={"X-Local-Date": date.today().isoformat(), "X-UTC-Offset": "0"},
                )
        return mock_llm, resp

    def test_card_thread_injects_calendar_into_system_prompt(self, client):
        """Calendar events appear in the system prompt when a mapping is configured."""
        card_id = _make_card("Prepare weekly report")
        self._make_mapping()

        with patch("routers.assist.get_personal_events", return_value=self._fake_events()):
            mock_llm, resp = self._stream_thread(client, card_id, "what's on my calendar tomorrow?")

        assert resp.status_code == 200
        call_args = mock_llm.return_value.chat.completions.create.call_args
        system_content = next(
            m["content"] for m in call_args[1]["messages"] if m["role"] == "system"
        )
        assert "Team standup" in system_content
        assert "### Upcoming calendar events" in system_content

    def test_card_thread_no_calendar_section_when_no_mapping(self, client):
        """When no CalendarMapping exists, get_personal_events is never called."""
        card_id = _make_card("Some task")

        with patch("routers.assist.get_personal_events") as mock_gpe:
            mock_llm, resp = self._stream_thread(client, card_id, "what's on my calendar?")

        assert resp.status_code == 200
        # assert_not_called proves no calendar data was fetched or injected
        mock_gpe.assert_not_called()

    def test_card_thread_shows_none_message_when_no_events(self, client):
        """When a mapping exists but no events fall in the window, the prompt says so."""
        card_id = _make_card("Weekend planning")
        self._make_mapping()

        with patch("routers.assist.get_personal_events", return_value=[]):
            mock_llm, resp = self._stream_thread(client, card_id, "what's on my calendar?")

        assert resp.status_code == 200
        system_content = next(
            m["content"] for m in mock_llm.return_value.chat.completions.create.call_args[1]["messages"]
            if m["role"] == "system"
        )
        assert "No events scheduled in the next 7 days" in system_content

    def test_card_thread_shows_error_message_when_fetch_fails(self, client):
        """When the iCal feed is unreachable, the prompt says 'could not fetch'."""
        card_id = _make_card("Incident review")
        self._make_mapping()

        with patch("routers.assist.get_personal_events", side_effect=Exception("Connection refused")):
            mock_llm, resp = self._stream_thread(client, card_id, "what's on my calendar?")

        assert resp.status_code == 200
        system_content = next(
            m["content"] for m in mock_llm.return_value.chat.completions.create.call_args[1]["messages"]
            if m["role"] == "system"
        )
        assert "Calendar temporarily unavailable" in system_content

    def test_global_assist_injects_calendar_into_user_message(self, client):
        """Calendar events appear in the user message context for global assist."""
        self._make_mapping()

        with patch("routers.assist.SessionLocal", TestSession):
            with patch("routers.assist.get_personal_events", return_value=self._fake_events()):
                with patch("routers.assist._maybe_web_search", return_value=""):
                    with patch("routers.assist.llm_client") as mock_llm:
                        stream_mock = MagicMock()
                        chunk = MagicMock()
                        chunk.choices[0].delta.content = "Team standup"
                        stream_mock.__iter__ = MagicMock(return_value=iter([chunk]))
                        mock_llm.return_value.chat.completions.create.return_value = stream_mock
                        resp = client.post(
                            "/api/assist/global",
                            json={"prompt": "what's on my calendar tomorrow?"},
                        )

        assert resp.status_code == 200
        call_args = mock_llm.return_value.chat.completions.create.call_args
        user_content = next(m["content"] for m in call_args[1]["messages"] if m["role"] == "user")
        assert "Team standup" in user_content
        assert "### Upcoming calendar events" in user_content


class TestGithubContextInjection:
    """GitHub issue body and comments are injected into the card thread system prompt."""

    def _make_card_with_issue(self, body="Fix the login bug", with_comments=True):
        with TestSession() as db:
            ext_id = "github:owner/repo/issues/42"
            eng = models.EngineeringItem(
                external_id=ext_id,
                title="Fix login bug",
                item_type="issue",
                repo="owner/repo",
                number=42,
                url="https://github.com/owner/repo/issues/42",
                state="open",
                body=body,
                synced_at=datetime.now(timezone.utc),
            )
            db.add(eng)
            db.flush()
            if with_comments:
                db.add(models.EngineeringItemComment(
                    item_id=eng.id,
                    github_id=1001,
                    author="alice",
                    body="We should also handle the OAuth flow here.",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                ))
            card = models.Card(
                title="Fix login bug",
                description="",
                section="today",
                external_id=ext_id,
                position=0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(card)
            db.commit()
            return card.id

    def test_github_body_injected_into_system_prompt(self):
        card_id = self._make_card_with_issue()
        app.dependency_overrides[get_db] = override_get_db
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        with patch("routers.assist.llm_client") as mock_llm:
            mock_llm.return_value.chat.completions.create.return_value = mock_stream
            with TestClient(app) as client:
                client.post(
                    f"/api/cards/{card_id}/thread/message",
                    json={"content": "What needs to be done here?"},
                )
        call_args = mock_llm.return_value.chat.completions.create.call_args
        system_content = next(m["content"] for m in call_args[1]["messages"] if m["role"] == "system")
        assert "### GitHub Issue" in system_content
        assert "owner/repo#42" in system_content
        assert "Fix the login bug" in system_content

    def test_github_comments_injected_into_system_prompt(self):
        card_id = self._make_card_with_issue()
        app.dependency_overrides[get_db] = override_get_db
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        with patch("routers.assist.llm_client") as mock_llm:
            mock_llm.return_value.chat.completions.create.return_value = mock_stream
            with TestClient(app) as client:
                client.post(
                    f"/api/cards/{card_id}/thread/message",
                    json={"content": "What needs to be done here?"},
                )
        call_args = mock_llm.return_value.chat.completions.create.call_args
        system_content = next(m["content"] for m in call_args[1]["messages"] if m["role"] == "system")
        assert "alice" in system_content
        assert "OAuth flow" in system_content

    def test_no_github_section_when_no_external_id(self):
        with TestSession() as db:
            card = models.Card(
                title="Plain task",
                description="",
                section="today",
                position=0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(card)
            db.commit()
            card_id = card.id
        app.dependency_overrides[get_db] = override_get_db
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        with patch("routers.assist.llm_client") as mock_llm:
            mock_llm.return_value.chat.completions.create.return_value = mock_stream
            with TestClient(app) as client:
                client.post(
                    f"/api/cards/{card_id}/thread/message",
                    json={"content": "Help me with this"},
                )
        call_args = mock_llm.return_value.chat.completions.create.call_args
        system_content = next(m["content"] for m in call_args[1]["messages"] if m["role"] == "system")
        # The injected header has format "### GitHub Issue: repo#N" (with colon + repo)
        # The system prompt rules reference "### GitHub Issue" without a colon, so we
        # check for the colon to distinguish injected content from the rule text.
        assert "### GitHub Issue:" not in system_content
        assert "### GitHub PR:" not in system_content
