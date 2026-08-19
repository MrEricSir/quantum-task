"""
Unit tests for GET/PUT /api/settings/navigation (routers/preferences.py).

Uses FastAPI's TestClient with an in-memory SQLite database — no server required.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base
from main import app
from deps import get_db
from routers.preferences import NAV_PAGE_IDS

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


class TestGetNavigationPreferences:
    def test_defaults_when_unset(self, client):
        res = client.get("/api/settings/navigation")
        assert res.status_code == 200
        body = res.json()
        assert body["order"] == NAV_PAGE_IDS
        assert body["default_page"] == "today"

    def test_fills_in_missing_pages(self, client):
        # Simulates an order saved before a new page (e.g. "engineering") existed --
        # bypasses PUT directly, since PUT itself requires a complete order.
        from settings import Settings
        with TestingSessionLocal() as db:
            settings = Settings(db)
            settings.set("nav_order", '["board", "today", "calendar", "health"]')
            db.commit()
        res = client.get("/api/settings/navigation")
        body = res.json()
        assert body["order"] == ["board", "today", "calendar", "health", "engineering"]

    def test_drops_unknown_saved_pages(self, client):
        from settings import Settings
        with TestingSessionLocal() as db:
            settings = Settings(db)
            settings.set("nav_order", '["today", "board", "calendar", "health", "engineering", "notes"]')
            db.commit()
        res = client.get("/api/settings/navigation")
        assert res.json()["order"] == NAV_PAGE_IDS

    def test_falls_back_to_today_for_unknown_default_page(self, client):
        from settings import Settings
        with TestingSessionLocal() as db:
            settings = Settings(db)
            settings.set("default_page", "notes")
            db.commit()
        res = client.get("/api/settings/navigation")
        assert res.json()["default_page"] == "today"


class TestSetNavigationPreferences:
    def test_roundtrip(self, client):
        new_order = ["calendar", "today", "board", "engineering", "health"]
        res = client.put("/api/settings/navigation", json={
            "order": new_order,
            "default_page": "calendar",
        })
        assert res.status_code == 200
        assert res.json() == {"order": new_order, "default_page": "calendar"}

        res = client.get("/api/settings/navigation")
        assert res.json() == {"order": new_order, "default_page": "calendar"}

    def test_rejects_incomplete_order(self, client):
        res = client.put("/api/settings/navigation", json={
            "order": ["today", "board"],
            "default_page": "today",
        })
        assert res.status_code == 400

    def test_rejects_unknown_page_in_order(self, client):
        res = client.put("/api/settings/navigation", json={
            "order": ["today", "board", "calendar", "health", "notes"],
            "default_page": "today",
        })
        assert res.status_code == 400

    def test_rejects_default_page_not_in_order(self, client):
        res = client.put("/api/settings/navigation", json={
            "order": NAV_PAGE_IDS,
            "default_page": "notes",
        })
        assert res.status_code == 400

    def test_rejects_duplicate_pages_in_order(self, client):
        res = client.put("/api/settings/navigation", json={
            "order": ["today", "today", "calendar", "health", "engineering"],
            "default_page": "today",
        })
        assert res.status_code == 400
