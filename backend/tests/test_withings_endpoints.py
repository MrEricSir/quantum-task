"""
Endpoint tests for the Withings router.

Covers: status, disconnect.
No real Withings API calls — all credentials are synthetic.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone

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


@pytest.fixture
def db():
    session = TestingSessionLocal()
    yield session
    session.close()


def _add_credentials(db, **overrides) -> models.WithingsCredentials:
    defaults = dict(
        access_token="tok_abc",
        token_type="Bearer",
        refresh_token="ref_xyz",
        userid=12345,
        client_id="client123",
        consumer_secret="secret456",
        expires_in=10800,
    )
    defaults.update(overrides)
    row = models.WithingsCredentials(**defaults)
    db.add(row)
    db.commit()
    return row


# ── GET /api/withings/status ──────────────────────────────────────────────────

class TestWithingsStatus:

    def test_not_connected_when_no_credentials(self, client):
        resp = client.get("/api/withings/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False
        assert data["last_synced"] is None

    def test_connected_when_credentials_exist(self, client, db):
        _add_credentials(db)
        resp = client.get("/api/withings/status")
        assert resp.status_code == 200
        assert resp.json()["connected"] is True

    def test_last_synced_none_when_not_synced(self, client, db):
        _add_credentials(db)
        resp = client.get("/api/withings/status")
        assert resp.json()["last_synced"] is None

    def test_last_synced_returned_when_set(self, client, db):
        ts = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
        _add_credentials(db, last_synced=ts)
        resp = client.get("/api/withings/status")
        data = resp.json()
        assert data["connected"] is True
        assert data["last_synced"] is not None
        assert "2026-06-20" in data["last_synced"]

    def test_last_synced_includes_explicit_utc_offset(self, client, db):
        """Regression test: last_synced is stored as a naive datetime that
        represents UTC by convention (SQLite strips tzinfo on save). If it's
        serialized without an explicit offset, `new Date(...)` on the
        frontend parses it as local time and displays the wrong clock time.
        The response must be self-describing."""
        ts = datetime(2026, 6, 20, 14, 30, 0, tzinfo=timezone.utc)
        _add_credentials(db, last_synced=ts)
        resp = client.get("/api/withings/status")
        last_synced = resp.json()["last_synced"]
        assert last_synced.endswith("+00:00") or last_synced.endswith("Z")
        assert datetime.fromisoformat(last_synced) == ts


# ── DELETE /api/withings/disconnect ──────────────────────────────────────────

class TestWithingsDisconnect:

    def test_disconnect_when_not_connected(self, client):
        resp = client.delete("/api/withings/disconnect")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_disconnect_removes_credentials(self, client, db):
        _add_credentials(db)
        assert db.query(models.WithingsCredentials).count() == 1

        resp = client.delete("/api/withings/disconnect")
        assert resp.status_code == 200

        db.expire_all()
        assert db.query(models.WithingsCredentials).count() == 0

    def test_status_shows_disconnected_after_disconnect(self, client, db):
        _add_credentials(db)
        client.delete("/api/withings/disconnect")

        resp = client.get("/api/withings/status")
        assert resp.json()["connected"] is False

    def test_disconnect_idempotent(self, client, db):
        _add_credentials(db)
        client.delete("/api/withings/disconnect")
        resp = client.delete("/api/withings/disconnect")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ── GET /api/withings/callback — CSRF state validation ────────────────────────

class TestWithingsCallbackState:

    def _mock_auth_url(self, monkeypatch, state="realstate123"):
        import routers.withings as withings_module
        from unittest.mock import patch

        monkeypatch.setattr(withings_module, "WITHINGS_CLIENT_ID", "cid")
        monkeypatch.setattr(withings_module, "WITHINGS_SECRET", "secret")
        return patch(
            "withings_api.WithingsAuth.get_authorize_url",
            return_value=f"https://account.withings.com/oauth2_user/authorize2?state={state}",
        )

    def test_callback_rejects_missing_state(self, client, db, monkeypatch):
        with self._mock_auth_url(monkeypatch):
            client.get("/api/withings/auth-url")
        resp = client.get("/api/withings/callback?code=somecode", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "error" in resp.headers["location"]
        assert db.query(models.WithingsCredentials).first() is None

    def test_callback_rejects_wrong_state(self, client, db, monkeypatch):
        with self._mock_auth_url(monkeypatch):
            client.get("/api/withings/auth-url")
        resp = client.get(
            "/api/withings/callback?code=somecode&state=attacker-supplied",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        assert "error" in resp.headers["location"]
        assert db.query(models.WithingsCredentials).first() is None

    def test_callback_accepts_matching_state(self, client, db, monkeypatch):
        import routers.withings as withings_module
        from unittest.mock import MagicMock, patch as _patch

        with self._mock_auth_url(monkeypatch, state="realstate123"):
            client.get("/api/withings/auth-url")

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
        with _patch.object(withings_module._requests, "post", return_value=resp):
            callback_resp = client.get(
                "/api/withings/callback?code=somecode&state=realstate123",
                follow_redirects=False,
            )
        assert callback_resp.status_code in (302, 307)
        assert "connected" in callback_resp.headers["location"]
        assert db.query(models.WithingsCredentials).first() is not None

    def test_state_is_single_use(self, client, db, monkeypatch):
        """A replayed callback with the same state (e.g. an attacker resending an
        intercepted redirect) must fail once the state has already been consumed."""
        import routers.withings as withings_module
        from unittest.mock import MagicMock, patch as _patch

        with self._mock_auth_url(monkeypatch, state="realstate123"):
            client.get("/api/withings/auth-url")

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
        with _patch.object(withings_module._requests, "post", return_value=resp):
            client.get(
                "/api/withings/callback?code=somecode&state=realstate123",
                follow_redirects=False,
            )
            replay_resp = client.get(
                "/api/withings/callback?code=somecode&state=realstate123",
                follow_redirects=False,
            )
        assert "error" in replay_resp.headers["location"]
