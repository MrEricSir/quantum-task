"""
Tests for /api/auth/* (login, check, logout) and AuthMiddleware.

Covers: session cookie issuance/validation, logout as a real revocation
(rotates the session secret rather than just clearing the browser's cookie),
login lockout after repeated failures, and the cookie's `secure` flag.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app_setting_keys as keys
import models
from main import app
from deps import get_db
from settings import Settings

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
def db():
    session = TestingSessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(monkeypatch):
    """Auth disabled (no AUTH_PASSWORD) unless a test opts in via auth_client."""
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setattr("main.AUTH_PASSWORD", "s3cret")
    monkeypatch.setattr("routers.auth.AUTH_PASSWORD", "s3cret")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── GET /api/auth/check ────────────────────────────────────────────────────────

class TestAuthCheck:

    def test_authed_true_when_no_password_configured(self, client):
        res = client.get("/api/auth/check")
        assert res.status_code == 200
        data = res.json()
        assert data["authed"] is True
        assert data["enabled"] is False

    def test_not_authed_without_a_session(self, auth_client):
        res = auth_client.get("/api/auth/check")
        data = res.json()
        assert data["enabled"] is True
        assert data["authed"] is False


# ── POST /api/auth/login ───────────────────────────────────────────────────────

class TestAuthLogin:

    def test_wrong_password_rejected(self, auth_client):
        res = auth_client.post("/api/auth/login", json={"password": "nope"})
        assert res.status_code == 401

    def test_correct_password_sets_session_cookie(self, auth_client):
        res = auth_client.post("/api/auth/login", json={"password": "s3cret"})
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert "session" in res.cookies

    def test_session_cookie_authenticates_subsequent_requests(self, auth_client):
        auth_client.post("/api/auth/login", json={"password": "s3cret"})
        res = auth_client.get("/api/auth/check")
        assert res.json()["authed"] is True

    def test_session_cookie_is_not_the_password_itself(self, auth_client):
        """Regression guard: the cookie must not be a value derivable purely from
        the login password (e.g. a plain HMAC of it) -- it should be an
        independently generated, revocable secret."""
        res = auth_client.post("/api/auth/login", json={"password": "s3cret"})
        cookie_value = res.cookies.get("session")
        assert cookie_value != "s3cret"

    def test_cookie_has_secure_flag_over_https_origin(self, monkeypatch):
        monkeypatch.setattr("main.AUTH_PASSWORD", "s3cret")
        monkeypatch.setattr("routers.auth.AUTH_PASSWORD", "s3cret")
        monkeypatch.setattr("routers.auth._COOKIE_SECURE", True)
        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as c:
            res = c.post("/api/auth/login", json={"password": "s3cret"})
            set_cookie_header = res.headers.get("set-cookie", "")
        app.dependency_overrides.clear()
        assert "secure" in set_cookie_header.lower()

    def test_no_password_configured_always_ok(self, client):
        res = client.post("/api/auth/login", json={"password": "anything"})
        assert res.status_code == 200
        assert res.json()["ok"] is True


# ── Login lockout ───────────────────────────────────────────────────────────────

class TestAuthLoginLockout:

    def test_locks_out_after_max_failed_attempts(self, auth_client):
        for _ in range(5):
            res = auth_client.post("/api/auth/login", json={"password": "wrong"})
            assert res.status_code == 401

        # 6th attempt, even with the correct password, is locked out
        res = auth_client.post("/api/auth/login", json={"password": "s3cret"})
        assert res.status_code == 429

    def test_successful_login_resets_the_failure_counter(self, auth_client, db):
        for _ in range(4):
            auth_client.post("/api/auth/login", json={"password": "wrong"})
        res = auth_client.post("/api/auth/login", json={"password": "s3cret"})
        assert res.status_code == 200

        s = Settings(db)
        assert s.auth_failed_attempts == 0

    def test_lockout_expires_after_the_window(self, auth_client, db):
        for _ in range(5):
            auth_client.post("/api/auth/login", json={"password": "wrong"})

        s = Settings(db)
        s.set(keys.AUTH_LOCKOUT_UNTIL, (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        db.commit()

        res = auth_client.post("/api/auth/login", json={"password": "s3cret"})
        assert res.status_code == 200


# ── POST /api/auth/logout ──────────────────────────────────────────────────────

class TestAuthLogout:

    def test_logout_clears_the_cookie(self, auth_client):
        auth_client.post("/api/auth/login", json={"password": "s3cret"})
        res = auth_client.post("/api/auth/logout")
        assert res.status_code == 200
        assert "session" not in res.cookies

    def test_logout_invalidates_the_session_everywhere(self, auth_client):
        """A session cookie captured before logout must stop working afterward --
        logout must be a real revocation, not just a local cookie deletion."""
        login_res = auth_client.post("/api/auth/login", json={"password": "s3cret"})
        old_cookie = login_res.cookies.get("session")

        auth_client.post("/api/auth/logout")

        auth_client.cookies.set("session", old_cookie)
        check_res = auth_client.get("/api/auth/check")
        assert check_res.json()["authed"] is False

    def test_logging_in_again_after_logout_issues_a_new_working_cookie(self, auth_client):
        auth_client.post("/api/auth/login", json={"password": "s3cret"})
        auth_client.post("/api/auth/logout")
        res = auth_client.post("/api/auth/login", json={"password": "s3cret"})
        assert res.status_code == 200
        check_res = auth_client.get("/api/auth/check")
        assert check_res.json()["authed"] is True
