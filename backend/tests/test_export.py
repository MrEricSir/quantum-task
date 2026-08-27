"""
Unit tests for GET /api/export (export/router.py, export/registry.py).

Uses FastAPI's TestClient with an in-memory SQLite database -- no server required.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base
from main import app
from deps import get_db
from export.registry import REGISTRY

TEST_DB_URL = "sqlite://"

test_engine = create_engine(
    TEST_DB_URL,
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


class TestExportEndpoint:
    def test_empty_db_returns_empty_sections(self, client):
        res = client.get("/api/export")
        assert res.status_code == 200
        body = res.json()
        for name in REGISTRY:
            assert body[name] == []
        assert "exported_at" in body

    def test_sets_download_headers(self, client):
        res = client.get("/api/export")
        assert res.headers["content-disposition"].startswith("attachment;")
        assert "quantum-task-export-" in res.headers["content-disposition"]

    def test_card_with_tags(self, client):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work", color="#3b82f6")
            db.add(tag)
            db.commit()
            tag_id = tag.id
            db.add(models.Card(title="Ship it", section="today", tags=[tag]))
            db.commit()

        res = client.get("/api/export")
        body = res.json()
        assert body["tags"] == [{"id": tag_id, "name": "work", "color": "#3b82f6", "is_project": False}]
        [card] = body["cards"]
        assert card["title"] == "Ship it"
        assert card["tags"] == ["work"]

    def test_habit_with_tags(self, client):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="personal", color="#10b981")
            db.add(tag)
            db.commit()
            db.add(models.Habit(name="Meditate", tags=[tag]))
            db.commit()

        res = client.get("/api/export")
        [habit] = res.json()["habits"]
        assert habit["name"] == "Meditate"
        assert habit["tags"] == ["personal"]

    def test_withings_credentials_never_exported(self, client):
        """Access/refresh tokens must never leave the app via export -- there's
        no ExportSection for WithingsCredentials at all, unlike measurements."""
        with TestingSessionLocal() as db:
            db.add(models.WithingsCredentials(
                access_token="secret-access", refresh_token="secret-refresh",
                userid=1, client_id="cid", consumer_secret="secret-consumer",
            ))
            db.commit()

        res = client.get("/api/export")
        assert "withings_credentials" not in res.json()
        assert "secret-access" not in res.text
        assert "secret-refresh" not in res.text
        assert "secret-consumer" not in res.text

    def test_settings_allowlist_excludes_secrets(self, client):
        from settings import Settings
        with TestingSessionLocal() as db:
            settings = Settings(db)
            settings.set("github_token", "ghp_supersecret")
            settings.set("nav_order", '["today", "board"]')
            db.commit()

        res = client.get("/api/export")
        settings_out = {s["key"]: s["value"] for s in res.json()["settings"]}
        assert "github_token" not in settings_out
        assert settings_out["nav_order"] == '["today", "board"]'
        assert "ghp_supersecret" not in res.text
