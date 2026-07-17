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
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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

    def test_skips_pending_and_running_jobs(self):
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

        assert result == "none"
        mock_send.assert_not_called()
