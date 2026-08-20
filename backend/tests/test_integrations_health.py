"""
Tests for:
  - routers.withings.get_auth_url / exchange_code (extracted for HealthProvider, Part 2 Step 2)
  - integrations.withings.WithingsProvider delegation

No real Withings API calls -- network calls and DB are mocked/monkeypatched per test.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import routers.withings as withings
from integrations.base import Measurement
from integrations.withings import WithingsProvider, DEFAULT_LOOKBACK_DAYS

test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=test_engine)
    yield
    models.Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db():
    session = TestingSessionLocal()
    yield session
    session.close()


# ── get_auth_url ────────────────────────────────────────────────────────────

class TestGetAuthUrl:
    def test_raises_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(withings, "WITHINGS_CLIENT_ID", "")
        monkeypatch.setattr(withings, "WITHINGS_SECRET", "")
        with pytest.raises(RuntimeError, match="not configured"):
            withings.get_auth_url()

    def test_returns_url_when_configured(self, monkeypatch):
        monkeypatch.setattr(withings, "WITHINGS_CLIENT_ID", "cid")
        monkeypatch.setattr(withings, "WITHINGS_SECRET", "secret")
        with patch("withings_api.WithingsAuth") as mock_auth_cls:
            mock_auth_cls.return_value.get_authorize_url.return_value = "https://withings.example/auth"
            assert withings.get_auth_url() == "https://withings.example/auth"


# ── exchange_code ─────────────────────────────────────────────────────────────

class TestExchangeCode:
    def test_saves_and_returns_credentials(self, db, monkeypatch):
        monkeypatch.setattr(withings, "WITHINGS_CLIENT_ID", "cid")
        monkeypatch.setattr(withings, "WITHINGS_SECRET", "secret")
        resp = MagicMock()
        resp.json.return_value = {
            "status": 0,
            "body": {
                "access_token": "tok_new",
                "refresh_token": "ref_new",
                "userid": 555,
                "expires_in": 10800,
            },
        }
        with patch.object(withings._requests, "post", return_value=resp):
            result = withings.exchange_code("auth_code_123", db)

        assert result["access_token"] == "tok_new"
        assert result["refresh_token"] == "ref_new"
        assert result["userid"] == 555
        row = db.query(models.WithingsCredentials).first()
        assert row is not None
        assert row.access_token == "tok_new"

    def test_raises_on_nonzero_status(self, db, monkeypatch):
        monkeypatch.setattr(withings, "WITHINGS_CLIENT_ID", "cid")
        monkeypatch.setattr(withings, "WITHINGS_SECRET", "secret")
        resp = MagicMock()
        resp.json.return_value = {"status": 401, "error": "invalid_grant"}
        with patch.object(withings._requests, "post", return_value=resp):
            with pytest.raises(RuntimeError, match="invalid_grant"):
                withings.exchange_code("bad_code", db)
        assert db.query(models.WithingsCredentials).first() is None


# ── WithingsProvider ────────────────────────────────────────────────────────

class TestWithingsProvider:
    def test_auth_url_delegates(self):
        provider = WithingsProvider()
        with patch.object(withings, "get_auth_url", return_value="https://auth") as mock:
            assert provider.auth_url() == "https://auth"
        mock.assert_called_once_with()

    def test_exchange_code_delegates(self, db):
        provider = WithingsProvider()
        with patch.object(withings, "exchange_code", return_value={"access_token": "x"}) as mock:
            result = provider.exchange_code("code123", db)
        mock.assert_called_once_with("code123", db)
        assert result == {"access_token": "x"}

    def test_sync_returns_readings_from_fetch_measurements(self):
        provider = WithingsProvider()
        creds = {"access_token": "tok"}
        fake_readings = {"steps": [Measurement(date="2026-05-01", value=100.0)]}
        with patch.object(withings, "fetch_measurements", return_value=(fake_readings, {})) as mock:
            result = provider.sync(creds)
        assert result == fake_readings
        args = mock.call_args[0]
        assert args[0] == creds
        assert isinstance(args[1], date) and isinstance(args[2], date)
        assert (args[2] - args[1]).days == DEFAULT_LOOKBACK_DAYS

    def test_sync_raises_when_all_sections_fail(self):
        provider = WithingsProvider()
        with patch.object(withings, "fetch_measurements", return_value=({}, {"activity": "boom"})):
            with pytest.raises(RuntimeError, match="boom"):
                provider.sync({"access_token": "tok"})

    def test_sync_returns_partial_readings_even_with_some_errors(self):
        provider = WithingsProvider()
        fake_readings = {"steps": [Measurement(date="2026-05-01", value=100.0)]}
        with patch.object(withings, "fetch_measurements", return_value=(fake_readings, {"sleep": "boom"})):
            result = provider.sync({"access_token": "tok"})
        assert result == fake_readings

    def test_refresh_delegates(self, db):
        provider = WithingsProvider()
        creds = {"access_token": "old"}
        with patch.object(withings, "_refresh_token", return_value={"access_token": "new"}) as mock:
            result = provider.refresh(creds, db)
        mock.assert_called_once_with(creds, db)
        assert result == {"access_token": "new"}
