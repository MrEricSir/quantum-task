"""
Tests for the Telegram router.

Covers:
  - GET /api/telegram/config   — returns defaults / saved values
  - PUT /api/telegram/config   — persists all fields
  - POST /api/telegram/test    — returns error when unconfigured; calls send and
                                 generate when configured (both mocked)
  - Bot reply functions        — add_note, read_note, query_completed, bulk_reschedule
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app_setting_keys as keys
import models
from main import app
from deps import get_db


# ── In-memory DB fixture ──────────────────────────────────────────────────────

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


# ── GET /api/telegram/config ──────────────────────────────────────────────────

class TestGetTelegramConfig:

    def test_returns_defaults_when_nothing_configured(self, client):
        res = client.get("/api/telegram/config")
        assert res.status_code == 200
        data = res.json()
        assert data["bot_token"] == ""
        assert data["chat_id"] == ""
        assert data["schedule_time"] == "07:30"
        assert data["tz_offset"] == 0
        assert data["weekly_review_schedule_time"] == "SUN:18:00"

    def test_returns_saved_values(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "123:ABC",
            "chat_id": "987654321",
            "schedule_time": "08:00",
            "tz_offset": -300,
        })
        res = client.get("/api/telegram/config")
        assert res.status_code == 200
        data = res.json()
        assert data["bot_token"] == "123:ABC"
        assert data["chat_id"] == "987654321"
        assert data["schedule_time"] == "08:00"
        assert data["tz_offset"] == -300


# ── PUT /api/telegram/config ──────────────────────────────────────────────────

class TestSaveTelegramConfig:

    def test_save_returns_ok(self, client):
        res = client.put("/api/telegram/config", json={
            "bot_token": "tok",
            "chat_id": "123",
            "schedule_time": "07:30",
            "tz_offset": 0,
        })
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_save_persists_across_requests(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "mytoken",
            "chat_id": "mychat",
            "schedule_time": "06:45",
            "tz_offset": 60,
        })
        res = client.get("/api/telegram/config")
        data = res.json()
        assert data["bot_token"] == "mytoken"
        assert data["chat_id"] == "mychat"
        assert data["schedule_time"] == "06:45"
        assert data["tz_offset"] == 60

    def test_save_overwrites_existing_values(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "first",
            "chat_id": "111",
            "schedule_time": "07:00",
            "tz_offset": 0,
        })
        client.put("/api/telegram/config", json={
            "bot_token": "second",
            "chat_id": "222",
            "schedule_time": "09:00",
            "tz_offset": -600,
        })
        data = client.get("/api/telegram/config").json()
        assert data["bot_token"] == "second"
        assert data["chat_id"] == "222"
        assert data["schedule_time"] == "09:00"
        assert data["tz_offset"] == -600

    def test_strips_whitespace_from_token_and_chat_id(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "  tok  ",
            "chat_id": " 123 ",
            "schedule_time": "07:30",
            "tz_offset": 0,
        })
        data = client.get("/api/telegram/config").json()
        assert data["bot_token"] == "tok"
        assert data["chat_id"] == "123"

    def test_saves_and_returns_custom_weekly_review_schedule(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok", "chat_id": "123", "schedule_time": "07:30", "tz_offset": 0,
            "weekly_review_schedule_time": "WED:09:00",
        })
        data = client.get("/api/telegram/config").json()
        assert data["weekly_review_schedule_time"] == "WED:09:00"

    def test_omitted_weekly_review_schedule_falls_back_to_default(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok", "chat_id": "123", "schedule_time": "07:30", "tz_offset": 0,
        })
        data = client.get("/api/telegram/config").json()
        assert data["weekly_review_schedule_time"] == "SUN:18:00"

    # Whether a saved weekly_review_schedule_time actually gates
    # check_weekly_review is covered by TestCheckWeeklyReview's
    # test_respects_custom_schedule_time below -- that class uses
    # BotTestSession (the scheduler tests' own in-memory engine), which is
    # deliberately separate from this class's HTTP `client` fixture engine,
    # so a single test can't cross both without one of the writes going to
    # the wrong database. Together, this class's round-trip tests (endpoint
    # persists the value) and that one (check_weekly_review honors whatever
    # Settings returns) establish the full chain without needing to mix engines.


# ── POST /api/telegram/test ───────────────────────────────────────────────────

class TestTelegramTest:

    def test_returns_error_when_not_configured(self, client):
        res = client.post("/api/telegram/test")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False
        assert "bot token" in data["error"].lower() or "chat id" in data["error"].lower()

    def test_returns_error_when_only_token_set(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok",
            "chat_id": "",
            "schedule_time": "07:30",
            "tz_offset": 0,
        })
        res = client.post("/api/telegram/test")
        assert res.json()["ok"] is False

    def test_sends_message_when_configured(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "valid_token",
            "chat_id": "123456",
            "schedule_time": "07:30",
            "tz_offset": 0,
        })
        with patch("telegram.router.generate_today_briefing", return_value="Good morning! Nothing scheduled.") as mock_gen, \
             patch("telegram.router.send_message", return_value=True) as mock_send:
            res = client.post("/api/telegram/test")

        assert res.status_code == 200
        assert res.json()["ok"] is True
        mock_gen.assert_called_once()
        mock_send.assert_called_once_with("valid_token", "123456", "Good morning! Nothing scheduled.")

    def test_returns_error_when_send_fails(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok",
            "chat_id": "123",
            "schedule_time": "07:30",
            "tz_offset": 0,
        })
        with patch("telegram.router.generate_today_briefing", return_value="Briefing text"), \
             patch("telegram.router.send_message", return_value=False):
            res = client.post("/api/telegram/test")

        assert res.json()["ok"] is False
        assert "token" in res.json()["error"].lower() or "failed" in res.json()["error"].lower()

    def test_returns_error_when_briefing_generation_fails(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok",
            "chat_id": "123",
            "schedule_time": "07:30",
            "tz_offset": 0,
        })
        with patch("telegram.router.generate_today_briefing", side_effect=RuntimeError("LLM down")):
            res = client.post("/api/telegram/test")

        data = res.json()
        assert data["ok"] is False
        assert "LLM down" in data["error"]

    def test_returns_error_when_briefing_returns_none(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok",
            "chat_id": "123",
            "schedule_time": "07:30",
            "tz_offset": 0,
        })
        with patch("telegram.router.generate_today_briefing", return_value=None):
            res = client.post("/api/telegram/test")

        assert res.json()["ok"] is False


# ── Bot reply function tests ───────────────────────────────────────────────────
# These test the core bot logic directly (no HTTP layer needed).

bot_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
BotTestSession = sessionmaker(autocommit=False, autoflush=False, bind=bot_engine)


@contextmanager
def _bot_session():
    db = BotTestSession()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _setup_bot_db():
    models.Base.metadata.create_all(bind=bot_engine)
    yield
    models.Base.metadata.drop_all(bind=bot_engine)


def _make_card(title, section="today", description=None, completed=False, completed_at=None, scheduled_at=None):
    with BotTestSession() as db:
        card = models.Card(
            title=title, section=section, description=description,
            completed=completed, completed_at=completed_at, scheduled_at=scheduled_at,
            position=0,
        )
        db.add(card)
        db.commit()
        return card.id


class TestBotAddNote:

    def test_adds_note_to_card_with_no_description(self):
        _make_card("Dentist appointment")
        from telegram.bot import _reply_add_note
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_add_note({"match_query": "dentist", "note": "bring insurance card"})
        assert "Dentist appointment" in reply
        assert "📝" in reply

        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(title="Dentist appointment").first()
            assert card.description == "bring insurance card"

    def test_appends_to_existing_description(self):
        _make_card("Dentist appointment", description="Call to confirm")
        from telegram.bot import _reply_add_note
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_add_note({"match_query": "dentist", "note": "bring insurance card"})

        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(title="Dentist appointment").first()
            assert "Call to confirm" in card.description
            assert "bring insurance card" in card.description

    def test_returns_error_when_task_not_found(self):
        from telegram.bot import _reply_add_note
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_add_note({"match_query": "nonexistent task", "note": "some note"})
        assert "Couldn't find" in reply

    def test_undo_restores_original_description(self):
        _make_card("Dentist appointment", description="Original note")
        from telegram.bot import _reply_add_note, _reply_undo, _sessions
        chat_id = "test_undo_note"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_add_note({"match_query": "dentist", "note": "new note"}, chat_id=chat_id)
            _reply_undo(chat_id)

        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(title="Dentist appointment").first()
            assert card.description == "Original note"


class TestBotReadNote:

    def test_returns_description(self):
        _make_card("Dentist appointment", description="Bring insurance card")
        from telegram.bot import _reply_read_note
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_read_note({"match_query": "dentist"})
        assert "Dentist appointment" in reply
        assert "Bring insurance card" in reply

    def test_no_notes_message(self):
        _make_card("Dentist appointment")
        from telegram.bot import _reply_read_note
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_read_note({"match_query": "dentist"})
        assert "no notes" in reply.lower()

    def test_not_found(self):
        from telegram.bot import _reply_read_note
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_read_note({"match_query": "xyz nonexistent"})
        assert "Couldn't find" in reply


class TestBotCompleted:

    def test_shows_completed_tasks_today(self):
        now = datetime.now(timezone.utc)
        _make_card("Write report", completed=True, completed_at=now)
        _make_card("Send email", completed=True, completed_at=now)
        from telegram.bot import _reply_completed
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_completed(tz_offset=0)
        assert "Write report" in reply
        assert "Send email" in reply
        assert "2 tasks" in reply

    def test_empty_message_when_nothing_done(self):
        from telegram.bot import _reply_completed
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_completed(tz_offset=0)
        assert "Nothing completed" in reply

    def test_excludes_tasks_completed_yesterday(self):
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        today = datetime.now(timezone.utc)
        _make_card("Old task", completed=True, completed_at=yesterday)
        _make_card("Today task", completed=True, completed_at=today)
        from telegram.bot import _reply_completed
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_completed(tz_offset=0)
        assert "Today task" in reply
        assert "Old task" not in reply


class TestBotBulkReschedule:

    def test_moves_overdue_tasks_to_week(self):
        past = datetime(2020, 1, 1, 12, 0)
        _make_card("Late task 1", section="today", scheduled_at=past)
        _make_card("Late task 2", section="today", scheduled_at=past)
        _make_card("Normal task", section="today")  # no scheduled_at — not overdue

        from telegram.bot import _reply_bulk_reschedule
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_bulk_reschedule({"filter": "overdue", "section": "week"}, tz_offset=0)

        assert "2 tasks" in reply
        assert "This Week" in reply

        with BotTestSession() as db:
            moved = db.query(models.Card).filter_by(section="week").all()
            assert len(moved) == 2
            normal = db.query(models.Card).filter_by(title="Normal task").first()
            assert normal.section == "today"

    def test_moves_all_today_tasks(self):
        _make_card("Task A", section="today")
        _make_card("Task B", section="today")
        from telegram.bot import _reply_bulk_reschedule
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_bulk_reschedule({"filter": "today", "section": "later"}, tz_offset=0)
        assert "2 tasks" in reply
        assert "Later" in reply

    def test_returns_message_when_no_tasks_match(self):
        from telegram.bot import _reply_bulk_reschedule
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_bulk_reschedule({"filter": "overdue", "section": "week"}, tz_offset=0)
        assert "No" in reply

    def test_undo_restores_all_moved_tasks(self):
        past = datetime(2020, 1, 1, 12, 0)
        _make_card("Late A", section="today", scheduled_at=past)
        _make_card("Late B", section="today", scheduled_at=past)
        from telegram.bot import _reply_bulk_reschedule, _reply_undo, _sessions
        chat_id = "test_bulk_undo"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_bulk_reschedule({"filter": "overdue", "section": "week"}, tz_offset=0, chat_id=chat_id)
            _reply_undo(chat_id)

        with BotTestSession() as db:
            cards = db.query(models.Card).all()
            for c in cards:
                assert c.section == "today"


# ── Bridge intent tests ───────────────────────────────────────────────────────

NOW_UTC = datetime.now(timezone.utc)


def _make_card_with_spec(title, spec=None, external_id=None):
    with BotTestSession() as db:
        card = models.Card(
            title=title, section="today", position=0, spec=spec,
            external_id=external_id,
        )
        db.add(card)
        db.commit()
        return card.id


class TestBotQueueBridge:

    def test_creates_job_for_card_with_spec(self):
        card_id = _make_card_with_spec("Auth feature", spec="## Fix\nOAuth")
        from telegram.bot import _reply_queue_bridge
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_queue_bridge({"match_query": "auth feature"})
        assert "Auth feature" in reply
        assert "Queued" in reply
        with BotTestSession() as db:
            job = db.query(models.BridgeJob).filter_by(card_id=card_id).first()
            assert job is not None
            assert job.status == "pending"

    def test_looks_up_card_by_numeric_id(self):
        card_id = _make_card_with_spec("Billing feature", spec="## Spec\nAdd invoices")
        from telegram.bot import _reply_queue_bridge
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_queue_bridge({"match_query": str(card_id)})
        assert "Billing feature" in reply
        assert "Queued" in reply

    def test_returns_error_when_card_not_found(self):
        from telegram.bot import _reply_queue_bridge
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_queue_bridge({"match_query": "nonexistent xyz card"})
        assert "Couldn't find" in reply

    def test_returns_error_when_card_has_no_spec(self):
        _make_card_with_spec("No spec card", spec=None)
        from telegram.bot import _reply_queue_bridge
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_queue_bridge({"match_query": "no spec"})
        assert "no spec" in reply.lower()

    def test_returns_error_when_query_is_empty(self):
        from telegram.bot import _reply_queue_bridge
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_queue_bridge({"match_query": ""})
        assert "Which card" in reply

    def test_disambiguation_when_multiple_matches(self):
        _make_card_with_spec("Auth login feature", spec="s")
        _make_card_with_spec("Auth oauth feature", spec="s")
        from telegram.bot import _reply_queue_bridge, _sessions
        chat_id = "test_bridge_disambig"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_queue_bridge({"match_query": "auth"}, chat_id=chat_id)
        assert "Which card" in reply
        assert _sessions[chat_id]["pending"]["action"] == "queue_bridge"

    def test_includes_job_id_in_reply(self):
        _make_card_with_spec("Dashboard feature", spec="## Spec")
        from telegram.bot import _reply_queue_bridge
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_queue_bridge({"match_query": "dashboard"})
        assert "#" in reply  # job ID formatted as #N

    def test_sets_last_card_in_session(self):
        _make_card_with_spec("Track feature", spec="## Spec")
        from telegram.bot import _reply_queue_bridge, _sessions
        chat_id = "test_bridge_lastcard"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_queue_bridge({"match_query": "track"}, chat_id=chat_id)
        assert _sessions[chat_id]["last_card"]["title"] == "Track feature"


# ── Capability-registry-backed handlers (mark_complete, complete_habit, ────────
# log_food, log_mood) -- previously had zero direct test coverage; added
# alongside the Level 2/3 registry refactor since these are exactly the
# functions whose signatures changed (query, chat_id) -> (intent, tz_offset, chat_id).

class TestBotMarkComplete:

    def test_returns_prompt_when_query_empty(self):
        from telegram.bot import _reply_complete
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_complete({"match_query": ""}, 0)
        assert "What task should I mark complete" in reply

    def test_returns_error_when_task_not_found(self):
        from telegram.bot import _reply_complete
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_complete({"match_query": "nonexistent xyz"}, 0)
        assert "Couldn't find" in reply

    def test_marks_single_match_complete(self):
        card_id = _make_card("Dentist appointment")
        from telegram.bot import _reply_complete
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_complete({"match_query": "dentist"}, 0)
        assert "Marked complete" in reply
        assert "Dentist appointment" in reply
        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(id=card_id).first()
            assert card.completed is True
            assert card.completed_at is not None

    def test_disambiguation_when_multiple_matches(self):
        _make_card("Auth login feature")
        _make_card("Auth oauth feature")
        from telegram.bot import _reply_complete, _sessions
        chat_id = "test_complete_disambig"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_complete({"match_query": "auth"}, 0, chat_id=chat_id)
        assert "Which task" in reply
        assert _sessions[chat_id]["pending"]["action"] == "complete"

    def test_pushes_undo(self):
        _make_card("Dentist appointment")
        from telegram.bot import _reply_complete, _reply_undo, _sessions
        chat_id = "test_complete_undo"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_complete({"match_query": "dentist"}, 0, chat_id=chat_id)
            undo_reply = _reply_undo(chat_id)
        assert "Dentist appointment" in undo_reply
        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(title="Dentist appointment").first()
            assert card.completed is False


class TestBotCompleteHabit:

    def test_returns_prompt_when_query_empty(self):
        from telegram.bot import _reply_complete_habit
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_complete_habit({"match_query": ""}, 0)
        assert "Which habit did you complete" in reply

    def test_returns_error_when_habit_not_found(self):
        _make_habit("Meditate")
        from telegram.bot import _reply_complete_habit
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_complete_habit({"match_query": "nonexistent xyz"}, 0)
        assert "No habit matching" in reply

    def test_marks_habit_complete(self):
        habit_id = _make_habit("Meditate")
        from telegram.bot import _reply_complete_habit
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_complete_habit({"match_query": "meditate"}, 0)
        assert "Meditate" in reply
        assert "done for today" in reply
        with BotTestSession() as db:
            assert db.query(models.HabitCompletion).filter_by(habit_id=habit_id).count() == 1

    def test_already_done_today(self):
        habit_id = _make_habit("Meditate")
        # _reply_complete_habit computes "today" from Settings(db).tz_offset,
        # which defaults to 0 (UTC) with no AppSetting row -- match that here
        # rather than the local system date, which may differ in this sandbox.
        _complete_habit(habit_id, datetime.now(timezone.utc).date())
        from telegram.bot import _reply_complete_habit
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_complete_habit({"match_query": "meditate"}, 0)
        assert "already marked done" in reply


class TestBotLogFood:
    """parse_food_entries() is the single shared split+enrich implementation
    used by both the webapp's food log (routers/food.py) and Telegram --
    mocked here the same way tests/test_food.py mocks it for the webapp side,
    so both suites exercise their own glue code without hitting a real LLM."""

    def _mock_items(self, *names):
        return [
            {"name": n, "category": "food", "source_text": n, "notes": None,
             "quality": None, "calories": None}
            for n in names
        ]

    def test_logs_a_single_food_entry(self):
        from telegram.bot import _reply_log_food
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot.parse_food_entries", return_value=self._mock_items("Yogurt and coffee")):
            reply = _reply_log_food({"raw_input": "yogurt and coffee"}, 0)
        assert "Food logged" in reply
        assert "Yogurt and coffee" in reply
        with BotTestSession() as db:
            entry = db.query(models.FoodEntry).filter_by(name="Yogurt and coffee").first()
            assert entry is not None

    def test_splits_a_multi_item_message_into_separate_entries(self):
        """The exact behavior this was built to fix: Telegram must split a
        multi-item message into multiple entries the same way the webapp's
        capture flow does, not store it as one combined entry."""
        from telegram.bot import _reply_log_food
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot.parse_food_entries",
                   return_value=self._mock_items("Bagel", "Coffee", "Banana")):
            reply = _reply_log_food({"raw_input": "had a bagel, coffee, and a banana"}, 0)
        assert "3 food items logged" in reply
        assert "Bagel" in reply and "Coffee" in reply and "Banana" in reply
        with BotTestSession() as db:
            names = {e.name for e in db.query(models.FoodEntry).all()}
            assert names == {"Bagel", "Coffee", "Banana"}

    def test_pushes_undo(self):
        from telegram.bot import _reply_log_food, _reply_undo, _sessions
        chat_id = "test_food_undo"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot.parse_food_entries", return_value=self._mock_items("Coffee")):
            _reply_log_food({"raw_input": "coffee"}, 0, chat_id=chat_id)
            undo_reply = _reply_undo(chat_id)
        assert "Coffee" in undo_reply
        with BotTestSession() as db:
            assert db.query(models.FoodEntry).filter_by(name="Coffee").count() == 0

    def test_undo_removes_every_entry_from_a_split_message(self):
        from telegram.bot import _reply_log_food, _reply_undo, _sessions
        chat_id = "test_food_undo_multi"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot.parse_food_entries",
                   return_value=self._mock_items("Bagel", "Coffee", "Banana")):
            _reply_log_food({"raw_input": "bagel, coffee, and a banana"}, 0, chat_id=chat_id)
            undo_reply = _reply_undo(chat_id)
        assert "3 food logs" in undo_reply
        with BotTestSession() as db:
            assert db.query(models.FoodEntry).count() == 0


class TestBotLogMood:

    def test_logs_mood_entry(self):
        from telegram.bot import _reply_log_mood
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_log_mood({"energy": 4, "note": "feeling focused"}, 0)
        assert "Energy logged" in reply
        assert "feeling focused" in reply
        with BotTestSession() as db:
            row = db.query(models.MoodLog).first()
            assert row.energy == 4
            assert row.note == "feeling focused"

    def test_clamps_out_of_range_energy(self):
        from telegram.bot import _reply_log_mood
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_log_mood({"energy": 99, "note": None}, 0)
        with BotTestSession() as db:
            assert db.query(models.MoodLog).first().energy == 5

    def test_defaults_to_okay_on_invalid_energy(self):
        from telegram.bot import _reply_log_mood
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_log_mood({"energy": "not a number", "note": None}, 0)
        with BotTestSession() as db:
            assert db.query(models.MoodLog).first().energy == 3

    def test_updates_existing_entry_for_today(self):
        from telegram.bot import _reply_log_mood
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_log_mood({"energy": 2, "note": "tired"}, 0)
            _reply_log_mood({"energy": 5, "note": "energized now"}, 0)
        with BotTestSession() as db:
            rows = db.query(models.MoodLog).all()
            assert len(rows) == 1
            assert rows[0].energy == 5
            assert rows[0].note == "energized now"


class TestBotLogWorkout:

    def test_logs_workout_entry(self):
        from telegram.bot import _reply_log_workout
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_log_workout(
                {"raw_input": "rowed 5000m", "type": "row", "value": 5000, "unit": "m"}, 0)
        assert "Workout logged" in reply
        assert "rowed 5000m" in reply
        with BotTestSession() as db:
            entry = db.query(models.WorkoutEntry).filter_by(raw_input="rowed 5000m").first()
            assert entry is not None
            assert entry.type == "row"
            assert entry.value == 5000
            assert entry.unit == "m"

    def test_unknown_type_falls_back_to_other(self):
        from telegram.bot import _reply_log_workout
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_log_workout({"raw_input": "did something", "type": "not_a_real_type"}, 0)
        with BotTestSession() as db:
            entry = db.query(models.WorkoutEntry).filter_by(raw_input="did something").first()
            assert entry.type == "other"

    def test_invalid_value_stored_as_null(self):
        from telegram.bot import _reply_log_workout
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_log_workout({"raw_input": "went for a walk", "type": "other", "value": "not a number"}, 0)
        with BotTestSession() as db:
            entry = db.query(models.WorkoutEntry).filter_by(raw_input="went for a walk").first()
            assert entry.value is None

    def test_pushes_undo(self):
        from telegram.bot import _reply_log_workout, _reply_undo, _sessions
        chat_id = "test_workout_undo"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession):
            _reply_log_workout({"raw_input": "bench pressed 185 lbs", "type": "strength", "value": 185, "unit": "lbs"}, 0, chat_id=chat_id)
            undo_reply = _reply_undo(chat_id)
        assert "bench pressed 185 lbs" in undo_reply
        with BotTestSession() as db:
            assert db.query(models.WorkoutEntry).filter_by(raw_input="bench pressed 185 lbs").count() == 0


def _fake_llm_client(text):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class TestBotWeather:

    def test_no_location_known_anywhere(self):
        from telegram.bot import _reply_weather, _sessions
        chat_id = "test_weather_none"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_weather(chat_id)
        assert "don't know your location" in reply.lower()

    def test_falls_back_to_last_known_webapp_location(self):
        from telegram.bot import _reply_weather, _sessions
        chat_id = "test_weather_webapp"
        _sessions.pop(chat_id, None)
        with BotTestSession() as db:
            from settings import Settings
            s = Settings(db)
            s.set(keys.LAST_KNOWN_LAT, "40.7128")
            s.set(keys.LAST_KNOWN_LON, "-74.0060")
            db.commit()

        fake_weather = {
            "emojis": "☀️", "description": "clear skies", "high": 75, "low": 60,
            "windy": False, "umbrella": False, "snow": False, "cold": False,
        }
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("weather.fetch_weather", return_value=fake_weather) as mock_fetch:
            reply = _reply_weather(chat_id)

        mock_fetch.assert_called_once_with(40.7128, -74.0060)
        assert "Clear skies" in reply
        assert "75" in reply and "60" in reply
        assert "last known location" in reply.lower()

    def test_prefers_shared_session_location_over_webapp_fallback(self):
        from telegram.bot import _reply_weather, _get_session, _sessions
        chat_id = "test_weather_shared"
        _sessions.pop(chat_id, None)
        _get_session(chat_id)["last_location"] = {
            "lat": 51.5074, "lon": -0.1278, "at": datetime.now(timezone.utc),
        }
        with BotTestSession() as db:
            from settings import Settings
            s = Settings(db)
            s.set(keys.LAST_KNOWN_LAT, "40.7128")
            s.set(keys.LAST_KNOWN_LON, "-74.0060")
            db.commit()

        fake_weather = {
            "emojis": "🌧️", "description": "rain", "high": 55, "low": 48,
            "windy": False, "umbrella": True, "snow": False, "cold": False,
        }
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("weather.fetch_weather", return_value=fake_weather) as mock_fetch:
            reply = _reply_weather(chat_id)

        mock_fetch.assert_called_once_with(51.5074, -0.1278)
        assert "Bring an umbrella" in reply
        # Shared-location replies don't carry the "last known location" caveat --
        # that's reserved for the webapp fallback specifically.
        assert "last known location" not in reply.lower()

    def test_fetch_failure_returns_friendly_message(self):
        from telegram.bot import _reply_weather, _get_session, _sessions
        chat_id = "test_weather_fetch_fail"
        _sessions.pop(chat_id, None)
        _get_session(chat_id)["last_location"] = {
            "lat": 1.0, "lon": 2.0, "at": datetime.now(timezone.utc),
        }
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("weather.fetch_weather", return_value=None):
            reply = _reply_weather(chat_id)
        assert "couldn't fetch" in reply.lower()


class TestBotAskSchedule:

    def test_empty_question_prompts_for_one(self):
        from telegram.bot import _reply_ask_schedule
        with patch("telegram.bot.SessionLocal", BotTestSession):
            reply = _reply_ask_schedule({"question": ""}, 0)
        assert "what would you like to know" in reply.lower()

    def test_nothing_scheduled_short_circuits_before_llm(self):
        from telegram.bot import _reply_ask_schedule
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot._fetch_cal_events_for_date", return_value=[]), \
             patch("telegram.bot.llm_client") as mock_llm:
            reply = _reply_ask_schedule({"question": "what's my last thing today?"}, 0)
        assert "nothing on your schedule" in reply.lower()
        mock_llm.assert_not_called()

    def test_general_question_goes_through_llm_with_context(self):
        # _reply_ask_schedule computes "today" internally from
        # datetime.now(timezone.utc) with tz_offset=0 -- i.e. UTC's current
        # date, not the local system's date.today(). Seed relative to that
        # same reference, or this flakes whenever UTC has already rolled
        # into the next day while the local system hasn't (evenings in any
        # timezone west of UTC).
        utc_today = datetime.now(timezone.utc).date()
        _make_card("Dentist appointment", section="today",
                   scheduled_at=datetime.combine(utc_today, datetime.min.time()).replace(hour=15))
        from telegram.bot import _reply_ask_schedule
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot._fetch_cal_events_for_date", return_value=[]), \
             patch("telegram.bot.llm_client", return_value=_fake_llm_client("Your last thing today is the dentist appointment at 3:00 PM.")):
            reply = _reply_ask_schedule({"question": "what's the last thing scheduled today?"}, 0)
        assert "dentist" in reply.lower()

    def test_duration_fit_question_is_answered_deterministically_not_by_llm(self):
        """This is the exact case where the LLM was verified unreliable (see
        the comment above the duration-fit branch in _reply_ask_schedule) --
        the reply must come from Python arithmetic, and the LLM must never
        even be called."""
        near_future = datetime.now(timezone.utc) + timedelta(hours=1)
        near_future_local = near_future.replace(tzinfo=None)
        _make_card("Team standup", section="today", scheduled_at=near_future_local)
        from telegram.bot import _reply_ask_schedule
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot._fetch_cal_events_for_date", return_value=[]), \
             patch("telegram.bot.llm_client") as mock_llm:
            reply = _reply_ask_schedule(
                {"question": "do I have time for a 20 minute nap?", "duration_minutes": 20}, 0)
        assert reply.startswith("✓ Yes")
        mock_llm.assert_not_called()

    def test_duration_fit_correctly_says_no_when_gap_too_small(self):
        near_future = datetime.now(timezone.utc) + timedelta(minutes=10)
        near_future_local = near_future.replace(tzinfo=None)
        _make_card("Team standup", section="today", scheduled_at=near_future_local)
        from telegram.bot import _reply_ask_schedule
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot._fetch_cal_events_for_date", return_value=[]), \
             patch("telegram.bot.llm_client") as mock_llm:
            reply = _reply_ask_schedule(
                {"question": "do I have time for a 20 minute nap?", "duration_minutes": 20}, 0)
        assert reply.startswith("✗")
        mock_llm.assert_not_called()

    def test_duration_fit_with_nothing_else_scheduled_is_yes(self):
        # An untimed today-card keeps the function past its "nothing on your
        # schedule at all" short-circuit, while leaving the timed-item
        # timeline empty -- so there's no future item to hit the gap against.
        _make_card("Read a book", section="today")
        from telegram.bot import _reply_ask_schedule
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot._fetch_cal_events_for_date", return_value=[]), \
             patch("telegram.bot.llm_client") as mock_llm:
            reply = _reply_ask_schedule(
                {"question": "do I have time for a workout?", "duration_minutes": 45}, 0)
        assert reply.startswith("✓ Yes")
        assert "nothing else is scheduled" in reply.lower()
        mock_llm.assert_not_called()


class TestRouteMessageCapabilityDispatch:
    """Confirms _route_message's generic by_telegram_action() lookup (Level 3)
    actually reaches each capability handler -- what would have silently
    broken if the dispatch replacement dropped or mis-wired a branch."""

    @pytest.mark.parametrize("action,intent,expected_substring", [
        ("mark_complete", {"match_query": "dentist"}, "Marked complete"),
        ("complete_habit", {"match_query": "meditate"}, "done for today"),
        ("log_food", {"raw_input": "toast"}, "Food logged"),
        ("log_mood", {"energy": 4, "note": None}, "Energy logged"),
        ("log_workout", {"raw_input": "ran 5 miles", "type": "run", "value": 5, "unit": "miles"}, "Workout logged"),
    ])
    def test_dispatches_to_the_right_handler(self, action, intent, expected_substring):
        _make_card("Dentist appointment")
        _make_habit("Meditate")
        from telegram.bot import _route_message
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot._parse_telegram_intent", return_value={"action": action, **intent}), \
             patch("telegram.bot._fetch_cal_events_for_date", return_value=[]):
            reply = _route_message("some free-text message", 0)
        assert expected_substring in reply

    def test_unrecognised_action_falls_through_to_capture(self):
        import schemas
        from telegram.bot import _route_message
        fake_item = schemas.ParsedCard(type="task", title="buy milk", section="later")
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot._parse_telegram_intent", return_value={"action": "capture"}), \
             patch("routers.cards.parse_bulk_text", return_value=[fake_item]):
            reply = _route_message("buy milk", 0)
        assert "milk" in reply.lower() or "added" in reply.lower() or "captured" in reply.lower()


class TestCaptureFromText:
    """_capture_from_text() is Telegram's 'capture' fallback, rebuilt to go
    through the exact same parse_bulk_text() the webapp's Quick Add uses --
    real splitting, resolve_dates/post_process corrections, recurrence,
    goal-setting, and habit creation, none of which the old hand-rolled
    single-item extraction supported. parse_bulk_text() itself is mocked
    throughout (it's covered by its own LLM-touching tests elsewhere); these
    tests exercise the dispatch/creation logic that runs on its output."""

    def _item(self, **kwargs):
        import schemas
        defaults = {"type": "task", "title": "Untitled", "section": "later"}
        return schemas.ParsedCard(**{**defaults, **kwargs})

    def test_single_task_creates_a_card(self):
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(title="buy milk", section="today")]):
            reply = _capture_from_text("buy milk", 0)
        assert "buy milk" in reply
        assert "Today" in reply
        assert "Send <b>undo</b> to remove it." in reply
        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(title="buy milk").first()
            assert card is not None
            assert card.section == "today"

    def test_splits_a_multi_item_message_into_separate_cards(self):
        from telegram.bot import _capture_from_text
        items = [self._item(title="call dentist"), self._item(title="buy eggs")]
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text", return_value=items):
            reply = _capture_from_text("call dentist and buy eggs", 0)
        assert "call dentist" in reply
        assert "buy eggs" in reply
        with BotTestSession() as db:
            titles = {c.title for c in db.query(models.Card).all()}
            assert titles == {"call dentist", "buy eggs"}

    def test_recurrence_rule_is_saved(self):
        """The old capture path had no recurrence_rule support at all."""
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(title="stretch", section="today", recurrence_rule="daily")]):
            _capture_from_text("stretch every day", 0)
        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(title="stretch").first()
            assert card.recurrence_rule == "daily"

    def test_suggested_tag_matches_existing_tag_case_insensitively(self):
        with BotTestSession() as db:
            db.add(models.Tag(name="Errands"))
            db.commit()
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(title="buy milk", suggested_tags=["errands"])]):
            _capture_from_text("buy milk", 0)
        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(title="buy milk").first()
            assert {t.name for t in card.tags} == {"Errands"}
            assert db.query(models.Tag).count() == 1  # no duplicate created

    def test_suggested_tag_with_no_existing_match_is_created(self):
        """The old capture path only did an exact match and silently dropped
        anything that didn't already exist -- never created new tags."""
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(title="buy milk", suggested_tags=["Groceries"])]):
            _capture_from_text("buy milk", 0)
        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(title="buy milk").first()
            assert {t.name for t in card.tags} == {"Groceries"}

    def test_habit_type_creates_a_habit_not_a_card(self):
        """The old capture path had no way to create a habit at all."""
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="habit", title="Meditate daily")]):
            reply = _capture_from_text("meditate every day", 0)
        assert "Meditate daily" in reply
        with BotTestSession() as db:
            assert db.query(models.Habit).filter_by(name="Meditate daily").first() is not None
            assert db.query(models.Card).count() == 0

    def test_steps_goal_creates_a_daily_steps_habit(self):
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="goal", title="steps goal",
                                             health_metric="steps", health_goal=10000)]):
            reply = _capture_from_text("set my step goal to 10000", 0)
        assert "10000" in reply
        with BotTestSession() as db:
            habit = db.query(models.Habit).filter_by(name="Daily Steps").first()
            assert habit is not None
            assert habit.health_metric == "steps"
            assert habit.health_goal == 10000

    def test_weight_goal_persists_via_withings_goals(self):
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="goal", title="weight goal",
                                             health_metric="weight", health_goal=75.0)]):
            _capture_from_text("set my weight goal to 75kg", 0)
        with BotTestSession() as db:
            from routers.withings import _load_goals
            assert _load_goals(db)["weight"] == 75.0

    def test_workout_type_parses_and_creates_a_workout_entry(self):
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="workout", title="ran 5k", source_text="ran 5k")]), \
             patch("routers.workouts._parse_workout",
                   return_value={"type": "run", "value": 5.0, "unit": "km", "notes": "A 5k run."}):
            reply = _capture_from_text("ran 5k", 0)
        assert "ran 5k" in reply
        with BotTestSession() as db:
            entry = db.query(models.WorkoutEntry).first()
            assert entry is not None
            assert entry.type == "run"
            assert entry.value == 5.0
            assert entry.notes == "A 5k run."

    def test_food_type_delegates_to_shared_food_logging(self):
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="food", title="a bagel", source_text="a bagel")]), \
             patch("telegram.bot.parse_food_entries",
                   return_value=[{"name": "Bagel", "category": "food", "source_text": "a bagel",
                                  "notes": None, "quality": None, "calories": None}]):
            reply = _capture_from_text("had a bagel", 0)
        assert "Bagel" in reply
        with BotTestSession() as db:
            assert db.query(models.FoodEntry).filter_by(name="Bagel").first() is not None

    def test_mood_type_delegates_to_shared_mood_logging(self):
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="mood", title="energy", energy=4, description="feeling good")]):
            reply = _capture_from_text("feeling good, energy 4", 0)
        assert "Energy logged" in reply
        with BotTestSession() as db:
            row = db.query(models.MoodLog).first()
            assert row.energy == 4

    def test_task_complete_type_delegates_to_existing_matcher(self):
        _make_card("Dentist appointment")
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="task_complete", title="dentist")]):
            reply = _capture_from_text("finished the dentist thing", 0)
        assert "Marked complete" in reply

    def test_habit_check_type_delegates_to_existing_matcher(self):
        _make_habit("Meditate")
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="habit_check", title="meditate")]):
            reply = _capture_from_text("did my meditation", 0)
        assert "done for today" in reply

    def test_assist_type_creates_nothing(self):
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="assist", title="what should I focus on today?")]):
            reply = _capture_from_text("what should I focus on today?", 0)
        assert "question" in reply.lower()
        with BotTestSession() as db:
            assert db.query(models.Card).count() == 0

    def test_falls_back_to_plain_capture_when_parsing_fails(self):
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text", side_effect=RuntimeError("LLM down")):
            reply = _capture_from_text("some free text", 0)
        assert "some free text" in reply
        with BotTestSession() as db:
            card = db.query(models.Card).filter_by(title="some free text").first()
            assert card is not None
            assert card.section == "today"

    def test_falls_back_to_plain_capture_when_no_items_returned(self):
        from telegram.bot import _capture_from_text
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text", return_value=[]):
            reply = _capture_from_text("some free text", 0)
        with BotTestSession() as db:
            assert db.query(models.Card).filter_by(title="some free text").first() is not None

    def test_undo_removes_every_card_from_a_split_message(self):
        from telegram.bot import _capture_from_text, _reply_undo, _sessions
        chat_id = "test_capture_multi_undo"
        _sessions.pop(chat_id, None)
        items = [self._item(title="call dentist"), self._item(title="buy eggs")]
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text", return_value=items):
            _capture_from_text("call dentist and buy eggs", 0, chat_id=chat_id)
            undo_reply = _reply_undo(chat_id)
        assert "Removed" in undo_reply
        with BotTestSession() as db:
            assert db.query(models.Card).count() == 0

    def test_undo_removes_a_created_habit(self):
        from telegram.bot import _capture_from_text, _reply_undo, _sessions
        chat_id = "test_capture_habit_undo"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="habit", title="Meditate daily")]):
            _capture_from_text("meditate every day", 0, chat_id=chat_id)
            _reply_undo(chat_id)
        with BotTestSession() as db:
            assert db.query(models.Habit).count() == 0

    def test_undo_removes_a_created_workout(self):
        from telegram.bot import _capture_from_text, _reply_undo, _sessions
        chat_id = "test_capture_workout_undo"
        _sessions.pop(chat_id, None)
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("routers.cards.parse_bulk_text",
                   return_value=[self._item(type="workout", title="ran 5k", source_text="ran 5k")]), \
             patch("routers.workouts._parse_workout",
                   return_value={"type": "run", "value": 5.0, "unit": "km", "notes": None}):
            _capture_from_text("ran 5k", 0, chat_id=chat_id)
            _reply_undo(chat_id)
        with BotTestSession() as db:
            assert db.query(models.WorkoutEntry).count() == 0


class TestCheckBridgeJobs:

    def test_returns_none_when_no_finished_jobs(self):
        from telegram.scheduler import check_bridge_jobs
        with BotTestSession() as db:
            result = check_bridge_jobs(db, token="tok", chat_id="123")
        assert result == "none"

    def test_notifies_on_done_job(self):
        card_id = _make_card_with_spec("My feature", spec="spec")
        with BotTestSession() as db:
            db.add(models.BridgeJob(
                card_id=card_id, status="done",
                result="https://github.com/owner/repo/pull/5",
                created_at=NOW_UTC, updated_at=NOW_UTC,
            ))
            db.commit()

        from telegram.scheduler import check_bridge_jobs
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_bridge_jobs(db, token="tok", chat_id="123")

        assert "1" in result
        call_text = mock_send.call_args[0][2]
        assert "My feature" in call_text
        assert "pull/5" in call_text

    def test_notifies_with_worktree_path(self):
        card_id = _make_card_with_spec("Path feature", spec="spec")
        with BotTestSession() as db:
            db.add(models.BridgeJob(
                card_id=card_id, status="done",
                branch_name="qtask/7-path-feature", agent_name="work-mac",
                worktree_path="/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-path-feature",
                created_at=NOW_UTC, updated_at=NOW_UTC,
            ))
            db.commit()

        from telegram.scheduler import check_bridge_jobs
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                check_bridge_jobs(db, token="tok", chat_id="123")

        call_text = mock_send.call_args[0][2]
        assert "qtask/7-path-feature" in call_text
        assert "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-path-feature" in call_text

    def test_notifies_on_error_job(self):
        card_id = _make_card_with_spec("Error feature", spec="spec")
        with BotTestSession() as db:
            db.add(models.BridgeJob(
                card_id=card_id, status="error",
                result="claude not found on PATH",
                created_at=NOW_UTC, updated_at=NOW_UTC,
            ))
            db.commit()

        from telegram.scheduler import check_bridge_jobs
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_bridge_jobs(db, token="tok", chat_id="123")

        call_text = mock_send.call_args[0][2]
        assert "failed" in call_text.lower() or "error" in call_text.lower()
        assert "Error feature" in call_text

    def test_does_not_double_notify(self):
        card_id = _make_card_with_spec("Once feature", spec="spec")
        with BotTestSession() as db:
            job = models.BridgeJob(
                card_id=card_id, status="done", result="PR #1",
                created_at=NOW_UTC, updated_at=NOW_UTC,
            )
            db.add(job)
            db.commit()
            job_id = job.id

        from telegram.scheduler import check_bridge_jobs
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                check_bridge_jobs(db, token="tok", chat_id="123")
            with BotTestSession() as db:
                check_bridge_jobs(db, token="tok", chat_id="123")

        assert mock_send.call_count == 1  # second call should find no new jobs

    def test_advances_watermark_after_notify(self):
        card_id = _make_card_with_spec("Watermark feature", spec="s")
        with BotTestSession() as db:
            db.add(models.BridgeJob(
                card_id=card_id, status="done", result="",
                created_at=NOW_UTC, updated_at=NOW_UTC,
            ))
            db.commit()

        from telegram.scheduler import check_bridge_jobs
        import app_setting_keys as keys
        with patch("telegram.scheduler.send_message", return_value=True):
            with BotTestSession() as db:
                check_bridge_jobs(db, token="tok", chat_id="123")

        with BotTestSession() as db:
            row = db.query(models.AppSetting).filter_by(
                key=keys.BRIDGE_LAST_NOTIFIED_JOB).first()
            assert row is not None
            assert int(row.value) > 0

    def test_skips_pending_jobs_notifies_running(self):
        card_id = _make_card_with_spec("Running feature", spec="s")
        with BotTestSession() as db:
            db.add(models.BridgeJob(
                card_id=card_id, status="running", result=None,
                created_at=NOW_UTC, updated_at=NOW_UTC,
            ))
            db.commit()

        from telegram.scheduler import check_bridge_jobs
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_bridge_jobs(db, token="tok", chat_id="123")

        # Running jobs get a "started" notification; no completion notification yet
        assert result == "notified: 1 event(s)"
        assert mock_send.call_count == 1
        text = mock_send.call_args[0][2]
        assert "Running feature" in text
        assert "▶" in text


# ── Proactive health/habit nudges ───────────────────────────────────────────────

TODAY = date.today()
EVENING_LOCAL = datetime.combine(TODAY, datetime.min.time()).replace(hour=20)


def _set_habit_reminder_time(time_str="20:00"):
    with BotTestSession() as db:
        from settings import Settings
        Settings(db).set(keys.HABIT_REMINDER_TIME, time_str)
        db.commit()


def _make_habit(name, days_old=30, health_metric=None, health_goal=None):
    with BotTestSession() as db:
        habit = models.Habit(
            name=name,
            created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
            health_metric=health_metric,
            health_goal=health_goal,
        )
        db.add(habit)
        db.commit()
        return habit.id


def _complete_habit(habit_id, day):
    with BotTestSession() as db:
        db.add(models.HabitCompletion(habit_id=habit_id, date=day.isoformat()))
        db.commit()


def _recompute_streak(habit_id):
    from streak import recompute_all
    with BotTestSession() as db:
        recompute_all(db, habit_id)
        db.commit()


def _make_food_entry(consumed_at, quality=None):
    with BotTestSession() as db:
        db.add(models.FoodEntry(
            raw_input="test item", name="test item", category="food",
            consumed_at=consumed_at, quality=quality,
        ))
        db.commit()


def _make_withings_reading(metric, day, value):
    with BotTestSession() as db:
        db.add(models.WithingsMeasurement(date=day.isoformat(), metric=metric, value=value))
        db.commit()


def _neutralize_food_log():
    """Log food today so the food-log-quiet signal doesn't fire in tests
    that are targeting a different signal."""
    _make_food_entry(datetime.combine(TODAY, datetime.min.time()).replace(hour=12))


class TestCheckHealthNudges:

    def test_skipped_when_hour_does_not_match(self):
        _set_habit_reminder_time("07:00")
        from telegram.scheduler import check_health_nudges
        with BotTestSession() as db:
            result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)
        assert result == "skipped"

    def test_skipped_when_nothing_to_flag(self):
        _set_habit_reminder_time()
        _neutralize_food_log()
        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)
        assert result == "skipped: nothing to flag"
        mock_send.assert_not_called()

    def test_streak_risk_flags_habit_with_active_streak_not_done_today(self):
        _set_habit_reminder_time()
        habit_id = _make_habit("Meditate")
        for i in (1, 2, 3):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "sent"
        text = mock_send.call_args[0][2]
        assert "Streak at risk" in text
        assert "Meditate" in text
        assert "3-day streak" in text

    def test_streak_risk_ignores_habit_below_min_streak(self):
        _set_habit_reminder_time()
        _neutralize_food_log()
        habit_id = _make_habit("Stretch")
        # 4/7 days completed (clears the going-cold threshold) but non-consecutive,
        # so the streak ending yesterday is only 1 (clears the streak-risk threshold)
        for i in (1, 3, 4, 6):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True):
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: nothing to flag"

    def test_streak_risk_ignores_habit_already_completed_today(self):
        _set_habit_reminder_time()
        _neutralize_food_log()
        habit_id = _make_habit("Journal")
        for i in (0, 1, 2, 3):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True):
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: nothing to flag"

    def test_going_cold_flags_low_completion_rate(self):
        _set_habit_reminder_time()
        habit_id = _make_habit("Drink water", days_old=30)
        for i in (2, 5):  # 2 of the last 7 days
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "sent"
        text = mock_send.call_args[0][2]
        assert "Slipping" in text
        assert "Drink water" in text
        assert "2/7" in text

    def test_going_cold_ignores_good_completion_rate(self):
        _set_habit_reminder_time()
        _neutralize_food_log()
        habit_id = _make_habit("Read", days_old=30)
        # 5/7 days completed, non-consecutive so the streak stays below the
        # streak-risk threshold — isolates the going-cold behavior being tested
        for i in (1, 2, 4, 5, 6):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True):
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: nothing to flag"

    def test_going_cold_ignores_habit_without_enough_history(self):
        _set_habit_reminder_time()
        _neutralize_food_log()
        habit_id = _make_habit("New habit", days_old=2)  # under the 7-day minimum age

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True):
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: nothing to flag"

    def test_food_log_quiet_flags_when_nothing_logged(self):
        _set_habit_reminder_time()
        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "sent"
        text = mock_send.call_args[0][2]
        assert "No food logged" in text

    def test_food_log_quiet_skipped_when_recently_logged(self):
        _set_habit_reminder_time()
        _make_food_entry(datetime.combine(TODAY, datetime.min.time()).replace(hour=12))

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True):
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: nothing to flag"

    def test_withings_drift_flags_low_steps_average(self):
        _set_habit_reminder_time()
        # days_old=2: under the going-cold minimum age, so a zero-completion
        # habit here doesn't also trip going-cold and muddy the assertions
        habit_id = _make_habit("Walk", days_old=2, health_metric="steps", health_goal=10000)
        for i in (1, 2, 3, 4):
            _make_withings_reading("steps", TODAY - timedelta(days=i), 4000)

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "sent"
        text = mock_send.call_args[0][2]
        assert "Trending off goal" in text
        assert "4,000" in text
        assert "10,000" in text

    def test_withings_drift_ignores_insufficient_readings(self):
        _set_habit_reminder_time()
        _neutralize_food_log()
        habit_id = _make_habit("Walk", days_old=2, health_metric="steps", health_goal=10000)
        _make_withings_reading("steps", TODAY - timedelta(days=1), 2000)  # only 1 reading

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True):
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: nothing to flag"

    def test_bundles_multiple_signals_into_one_message(self):
        _set_habit_reminder_time()
        habit_id = _make_habit("Meditate")
        for i in (1, 2, 3):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)
        # Food log is quiet by default (no entries created in this test)

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "sent"
        assert mock_send.call_count == 1
        text = mock_send.call_args[0][2]
        assert "Meditate" in text
        assert "No food logged" in text

    def test_cooldown_prevents_resend_within_window(self):
        _set_habit_reminder_time()
        habit_id = _make_habit("Meditate")
        for i in (1, 2, 3):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: nothing to flag"
        assert mock_send.call_count == 1

    def test_cooldown_expires_and_resends_after_window(self):
        _set_habit_reminder_time()
        with BotTestSession() as db:
            from settings import Settings
            import json as _json
            stale = (TODAY - timedelta(days=10)).isoformat()
            Settings(db).set(keys.HEALTH_NUDGES_SENT, _json.dumps({"food_log_quiet": stale}))
            db.commit()

        from telegram.scheduler import check_health_nudges
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_health_nudges(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "sent"
        assert mock_send.call_count == 1
        text = mock_send.call_args[0][2]
        assert "No food logged" in text


# ── Streak milestone celebrations (habit, food quality, task completion) ────────

class TestCheckStreakMilestones:

    def test_no_alert_when_no_habit_at_milestone(self):
        habit_id = _make_habit("Meditate")
        _complete_habit(habit_id, TODAY)  # streak of 1, not a milestone
        _recompute_streak(habit_id)

        from telegram.scheduler import check_streak_milestones
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_streak_milestones(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: no milestones"
        mock_send.assert_not_called()

    def test_sends_alert_when_habit_hits_milestone(self):
        habit_id = _make_habit("Meditate")
        for i in (0, 1, 2):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)

        from telegram.scheduler import check_streak_milestones
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_streak_milestones(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "sent: 1 milestone(s)"
        text = mock_send.call_args[0][2]
        assert "Meditate" in text
        assert "3-day" in text

    def test_does_not_resend_same_milestone_same_day(self):
        habit_id = _make_habit("Meditate")
        for i in (0, 1, 2):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)

        from telegram.scheduler import check_streak_milestones
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                check_streak_milestones(db, "tok", "123", EVENING_LOCAL, TODAY)
            with BotTestSession() as db:
                result = check_streak_milestones(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: no milestones"
        assert mock_send.call_count == 1

    def test_first_ever_milestone_is_a_personal_best(self):
        habit_id = _make_habit("Meditate")
        for i in (0, 1, 2):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)

        from telegram.scheduler import check_streak_milestones
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                check_streak_milestones(db, "tok", "123", EVENING_LOCAL, TODAY)

        text = mock_send.call_args[0][2]
        assert "New personal best" in text

    def test_repeated_milestone_after_streak_reset_is_not_a_new_best(self):
        habit_id = _make_habit("Meditate", days_old=30)
        # First run: 3-day streak ending 18 days ago, sets the watermark at 3
        first_end = TODAY - timedelta(days=18)
        for i in (0, 1, 2):
            _complete_habit(habit_id, first_end - timedelta(days=i))
        _recompute_streak(habit_id)
        from telegram.scheduler import check_streak_milestones
        with patch("telegram.scheduler.send_message", return_value=True):
            with BotTestSession() as db:
                check_streak_milestones(db, "tok", "123", EVENING_LOCAL, first_end)

        # Gap (day 17 not completed), then a second, separate 3-day run
        # ending 13 days ago — same peak value, not a new record.
        second_end = TODAY - timedelta(days=13)
        for i in (0, 1, 2):
            _complete_habit(habit_id, second_end - timedelta(days=i))
        _recompute_streak(habit_id)

        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_streak_milestones(db, "tok", "123", EVENING_LOCAL, second_end)

        assert result == "sent: 1 milestone(s)"
        text = mock_send.call_args[0][2]
        assert "New personal best" not in text

    def test_food_quality_streak_triggers_at_milestone(self):
        for i in (0, 1, 2):
            _make_food_entry(
                datetime.combine(TODAY - timedelta(days=i), datetime.min.time()).replace(hour=12),
                quality=8,
            )

        from telegram.scheduler import check_streak_milestones
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_streak_milestones(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "sent: 1 milestone(s)"
        text = mock_send.call_args[0][2]
        assert "food quality" in text
        assert "3-day" in text

    def test_food_quality_streak_ignores_low_quality_days(self):
        for i in (0, 1, 2):
            _make_food_entry(
                datetime.combine(TODAY - timedelta(days=i), datetime.min.time()).replace(hour=12),
                quality=3,  # below the threshold
            )

        from telegram.scheduler import check_streak_milestones
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_streak_milestones(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "skipped: no milestones"
        mock_send.assert_not_called()

    def test_task_completion_streak_triggers_at_milestone(self):
        for i in (0, 1, 2):
            _make_card(
                f"Task {i}", completed=True,
                completed_at=datetime.combine(TODAY - timedelta(days=i), datetime.min.time()).replace(hour=12),
            )

        from telegram.scheduler import check_streak_milestones
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_streak_milestones(db, "tok", "123", EVENING_LOCAL, TODAY)

        assert result == "sent: 1 milestone(s)"
        text = mock_send.call_args[0][2]
        assert "task completion" in text
        assert "3-day" in text


# ── Weekly review ────────────────────────────────────────────────────────────

def _next_weekday(d, target_weekday):
    """target_weekday: Monday=0 ... Sunday=6 (Python's date.weekday() convention)."""
    return d + timedelta(days=(target_weekday - d.weekday()) % 7)


SUNDAY = _next_weekday(TODAY, 6)
SUNDAY_1800 = datetime.combine(SUNDAY, datetime.min.time()).replace(hour=18, minute=30)
SUNDAY_1700 = datetime.combine(SUNDAY, datetime.min.time()).replace(hour=17, minute=30)


class TestGenerateWeeklyReview:

    def test_includes_completed_task_count(self):
        for i in range(3):
            _make_card(
                f"Task {i}", completed=True,
                completed_at=datetime.combine(TODAY - timedelta(days=i), datetime.min.time()).replace(hour=12),
            )
        from telegram.scheduler import generate_weekly_review
        fake_client = _fake_llm_client("Great week overall!")
        with patch("telegram.scheduler.SessionLocal", BotTestSession), \
             patch("deps.llm_client", return_value=fake_client):
            text = generate_weekly_review(TODAY, 0)
        assert text is not None
        assert "Great week overall!" in text
        assert "Weekly review" in text
        user_content = fake_client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "Tasks completed: 3" in user_content

    def test_includes_habit_completion_rate_and_streak(self):
        habit_id = _make_habit("Meditate")
        for i in (0, 1, 2):
            _complete_habit(habit_id, TODAY - timedelta(days=i))
        _recompute_streak(habit_id)
        from telegram.scheduler import generate_weekly_review
        fake_client = _fake_llm_client("Nice consistency.")
        with patch("telegram.scheduler.SessionLocal", BotTestSession), \
             patch("deps.llm_client", return_value=fake_client):
            generate_weekly_review(TODAY, 0)
        user_content = fake_client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "Meditate: 3/7 days" in user_content
        assert "3-day streak" in user_content

    def test_includes_dismissed_experiment_from_this_week(self):
        # dismissed_at is seeded as real UTC "now" and generate_weekly_review
        # compares it (tz_offset=0, so no adjustment) against the "today" it's
        # called with -- pass the same UTC date here, not the local system's
        # TODAY, or this flakes whenever UTC has already rolled into the next
        # day while the local system hasn't.
        utc_today = datetime.now(timezone.utc).date()
        with BotTestSession() as db:
            db.add(models.HealthExperiment(
                week="2026-W01", text="Row 2 mi/day instead of 1 mi/day", status="dismissed",
                dismissed_at=datetime.now(timezone.utc), needs_habit=False,
                weight_delta=-0.1, weight_baseline=0.05,
            ))
            db.commit()
        from telegram.scheduler import generate_weekly_review
        fake_client = _fake_llm_client("Good progress on the rowing experiment.")
        with patch("telegram.scheduler.SessionLocal", BotTestSession), \
             patch("deps.llm_client", return_value=fake_client):
            generate_weekly_review(utc_today, 0)
        user_content = fake_client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "Row 2 mi/day instead of 1 mi/day" in user_content
        assert "improved" in user_content

    def test_ignores_experiment_dismissed_outside_the_week(self):
        with BotTestSession() as db:
            db.add(models.HealthExperiment(
                week="2025-W01", text="Old experiment", status="dismissed",
                dismissed_at=datetime.now(timezone.utc) - timedelta(days=30), needs_habit=False,
            ))
            db.commit()
        from telegram.scheduler import generate_weekly_review
        fake_client = _fake_llm_client("Solid week.")
        with patch("telegram.scheduler.SessionLocal", BotTestSession), \
             patch("deps.llm_client", return_value=fake_client):
            generate_weekly_review(TODAY, 0)
        user_content = fake_client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "Old experiment" not in user_content

    def test_returns_none_on_llm_failure(self):
        from telegram.scheduler import generate_weekly_review
        with patch("telegram.scheduler.SessionLocal", BotTestSession), \
             patch("deps.llm_client", side_effect=RuntimeError("LLM down")):
            text = generate_weekly_review(TODAY, 0)
        assert text is None


class TestCheckWeeklyReview:

    def test_skipped_when_day_does_not_match(self):
        from telegram.scheduler import check_weekly_review
        monday = SUNDAY + timedelta(days=1)
        now_local = datetime.combine(monday, datetime.min.time()).replace(hour=18, minute=30)
        with BotTestSession() as db:
            result = check_weekly_review(db, "tok", "123", 0, now_local, monday)
        assert result == "skipped"

    def test_skipped_when_hour_does_not_match(self):
        from telegram.scheduler import check_weekly_review
        with BotTestSession() as db:
            result = check_weekly_review(db, "tok", "123", 0, SUNDAY_1700, SUNDAY)
        assert result == "skipped"

    def test_sends_on_configured_day_and_hour(self):
        from telegram.scheduler import check_weekly_review
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send, \
             patch("telegram.scheduler.generate_weekly_review", return_value="<b>Review</b>\n\nGreat week.") as mock_gen:
            with BotTestSession() as db:
                result = check_weekly_review(db, "tok", "123", 0, SUNDAY_1800, SUNDAY)
        assert result == "sent"
        mock_gen.assert_called_once_with(SUNDAY, 0)
        mock_send.assert_called_once_with("tok", "123", "<b>Review</b>\n\nGreat week.")

    def test_does_not_resend_same_week(self):
        from telegram.scheduler import check_weekly_review
        with patch("telegram.scheduler.send_message", return_value=True), \
             patch("telegram.scheduler.generate_weekly_review", return_value="text"):
            with BotTestSession() as db:
                check_weekly_review(db, "tok", "123", 0, SUNDAY_1800, SUNDAY)
            with BotTestSession() as db:
                result = check_weekly_review(db, "tok", "123", 0, SUNDAY_1800, SUNDAY)
        assert result == "already_sent"

    def test_respects_custom_schedule_time(self):
        with BotTestSession() as db:
            from settings import Settings
            Settings(db).set(keys.WEEKLY_REVIEW_SCHEDULE_TIME, "WED:09:00")
            db.commit()
        from telegram.scheduler import check_weekly_review
        wednesday = _next_weekday(TODAY, 2)
        wed_0930 = datetime.combine(wednesday, datetime.min.time()).replace(hour=9, minute=30)
        with patch("telegram.scheduler.send_message", return_value=True), \
             patch("telegram.scheduler.generate_weekly_review", return_value="text"):
            with BotTestSession() as db:
                result = check_weekly_review(db, "tok", "123", 0, wed_0930, wednesday)
            assert result == "sent"
            # The default Sunday time no longer matches once overridden
            with BotTestSession() as db2:
                result2 = check_weekly_review(db2, "tok", "123", 0, SUNDAY_1800, SUNDAY)
        assert result2 == "skipped"

    def test_generation_failure_returns_error(self):
        from telegram.scheduler import check_weekly_review
        with patch("telegram.scheduler.generate_weekly_review", return_value=None):
            with BotTestSession() as db:
                result = check_weekly_review(db, "tok", "123", 0, SUNDAY_1800, SUNDAY)
        assert result == "error: generation failed"


class TestWeeklyReviewEndpoint:

    def test_returns_error_when_not_configured(self, client):
        res = client.post("/api/telegram/test-weekly-review")
        assert res.status_code == 200
        assert res.json()["ok"] is False

    def test_sends_message_when_configured(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "valid_token", "chat_id": "123456",
            "schedule_time": "07:30", "tz_offset": 0,
        })
        with patch("telegram.router.generate_weekly_review", return_value="Weekly review text") as mock_gen, \
             patch("telegram.router.send_message", return_value=True) as mock_send:
            res = client.post("/api/telegram/test-weekly-review")
        assert res.json()["ok"] is True
        mock_gen.assert_called_once()
        mock_send.assert_called_once_with("valid_token", "123456", "Weekly review text")

    def test_returns_error_when_generation_raises(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok", "chat_id": "123", "schedule_time": "07:30", "tz_offset": 0,
        })
        with patch("telegram.router.generate_weekly_review", side_effect=RuntimeError("LLM down")):
            res = client.post("/api/telegram/test-weekly-review")
        assert "LLM down" in res.json()["error"]

    def test_returns_error_when_generation_returns_none(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok", "chat_id": "123", "schedule_time": "07:30", "tz_offset": 0,
        })
        with patch("telegram.router.generate_weekly_review", return_value=None):
            res = client.post("/api/telegram/test-weekly-review")
        assert res.json()["ok"] is False

    def test_returns_error_when_send_fails(self, client):
        client.put("/api/telegram/config", json={
            "bot_token": "tok", "chat_id": "123", "schedule_time": "07:30", "tz_offset": 0,
        })
        with patch("telegram.router.generate_weekly_review", return_value="text"), \
             patch("telegram.router.send_message", return_value=False):
            res = client.post("/api/telegram/test-weekly-review")
        assert res.json()["ok"] is False


class TestCheckEveningSummary:
    """github_sync.py marks a card BOTH completed and archived in the same step
    when its linked GitHub issue/PR closes -- the evening summary's completed-
    today query used to also filter on archived == False, which silently
    dropped every GitHub-ticket task from the summary on the day it closed."""

    def _set_reminder_time(self, hh_mm="19:00"):
        from settings import Settings
        with BotTestSession() as db:
            Settings(db).set(keys.HABIT_REMINDER_TIME, hh_mm)
            db.commit()

    def test_skipped_when_hour_does_not_match(self):
        from telegram.scheduler import check_evening_summary
        self._set_reminder_time("19:00")
        now_local = datetime.combine(TODAY, datetime.min.time()).replace(hour=10)
        with BotTestSession() as db:
            result = check_evening_summary(db, "tok", "123", 0, now_local, TODAY)
        assert result == "skipped"

    def test_includes_a_completed_and_archived_github_linked_card(self):
        now_local = datetime.combine(TODAY, datetime.min.time()).replace(hour=19, minute=5)
        with BotTestSession() as db:
            db.add(models.Card(
                title="Fix the flaky test", section="today", position=0,
                completed=True, archived=True, completed_at=now_local,
                external_id="github:owner/repo/issues/42",
            ))
            db.commit()
        self._set_reminder_time("19:00")
        from telegram.scheduler import check_evening_summary
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                result = check_evening_summary(db, "tok", "123", 0, now_local, TODAY)
        assert result == "sent"
        sent_text = mock_send.call_args[0][2]
        assert "Fix the flaky test" in sent_text
        assert "1 task" in sent_text

    def test_still_excludes_a_task_completed_on_a_different_day(self):
        now_local = datetime.combine(TODAY, datetime.min.time()).replace(hour=19, minute=5)
        yesterday = datetime.combine(TODAY - timedelta(days=1), datetime.min.time()).replace(hour=12)
        with BotTestSession() as db:
            db.add(models.Card(
                title="Old task", section="today", position=0,
                completed=True, archived=True, completed_at=yesterday,
            ))
            db.commit()
        self._set_reminder_time("19:00")
        from telegram.scheduler import check_evening_summary
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            with BotTestSession() as db:
                check_evening_summary(db, "tok", "123", 0, now_local, TODAY)
        sent_text = mock_send.call_args[0][2]
        assert "Old task" not in sent_text
        assert "No tasks completed today" in sent_text

    def test_does_not_resend_same_day(self):
        now_local = datetime.combine(TODAY, datetime.min.time()).replace(hour=19, minute=5)
        self._set_reminder_time("19:00")
        from telegram.scheduler import check_evening_summary
        with patch("telegram.scheduler.send_message", return_value=True):
            with BotTestSession() as db:
                check_evening_summary(db, "tok", "123", 0, now_local, TODAY)
            with BotTestSession() as db:
                result = check_evening_summary(db, "tok", "123", 0, now_local, TODAY)
        assert result == "already_sent"


class TestHandleUpdateDedup:
    """Telegram resends an update if our webhook response doesn't arrive in time
    (e.g. a slow LLM call during a Cloud Run cold start). Without update_id
    tracking, a slow-but-successful reply gets reprocessed -- and re-sent -- on
    every retry, which is what produced repeated duplicate replies in practice."""

    def _configure_bot(self, chat_id="12345"):
        with BotTestSession() as db:
            db.add(models.AppSetting(key=keys.TELEGRAM_BOT_TOKEN, value="test-token"))
            db.add(models.AppSetting(key=keys.TELEGRAM_CHAT_ID, value=chat_id))
            db.commit()

    def _update(self, update_id, text="help", chat_id="12345"):
        return {
            "update_id": update_id,
            "message": {"text": text, "chat": {"id": int(chat_id)}},
        }

    def test_processes_a_new_update(self):
        from telegram.bot import handle_update
        self._configure_bot()
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot.send_message", return_value=True) as mock_send:
            handle_update(self._update(100))
        assert mock_send.call_count == 1

    def test_skips_a_retried_update_with_the_same_update_id(self):
        from telegram.bot import handle_update
        self._configure_bot()
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot.send_message", return_value=True) as mock_send:
            handle_update(self._update(100))
            handle_update(self._update(100))
            handle_update(self._update(100))
        assert mock_send.call_count == 1

    def test_skips_a_stale_retry_that_arrives_after_a_newer_update(self):
        from telegram.bot import handle_update
        self._configure_bot()
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot.send_message", return_value=True) as mock_send:
            handle_update(self._update(105))
            handle_update(self._update(100))
        assert mock_send.call_count == 1

    def test_processes_each_distinct_update_id(self):
        from telegram.bot import handle_update
        self._configure_bot()
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot.send_message", return_value=True) as mock_send:
            handle_update(self._update(100))
            handle_update(self._update(101))
        assert mock_send.call_count == 2

    def test_updates_without_an_update_id_are_never_deduped(self):
        # e.g. /api/telegram/simulate-message's fake_update, which has no update_id
        from telegram.bot import handle_update
        self._configure_bot()
        update = {"message": {"text": "help", "chat": {"id": 12345}}}
        with patch("telegram.bot.SessionLocal", BotTestSession), \
             patch("telegram.bot.send_message", return_value=True) as mock_send:
            handle_update(update)
            handle_update(update)
        assert mock_send.call_count == 2
