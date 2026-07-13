"""
Tests for the Telegram briefing router.

Covers:
  - GET /api/telegram/config   — returns defaults / saved values
  - PUT /api/telegram/config   — persists all fields
  - POST /api/telegram/test    — returns error when unconfigured; calls send and
                                 generate when configured (both mocked)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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
