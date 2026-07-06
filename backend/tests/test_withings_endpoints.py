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
