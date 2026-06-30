"""
Tests for the food log router (routers/food.py).

Uses FastAPI TestClient with an in-memory SQLite DB.
LLM parsing is mocked to avoid network calls.
"""
import sys
import os
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Base
import models
from routers import food as food_router
from deps import get_db

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(food_router.router)
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


# ── Mock LLM ──────────────────────────────────────────────────────────────────

_PARSED = {
    "name": "donut",
    "category": "food",
    "meal_type": "snack",
    "notes": "High in sugar and refined carbs.",
    "quality": 3,
}


def _mock_parse(raw: str) -> dict:
    return {**_PARSED, "name": raw[:20]}  # name reflects input for traceability


# ── CRUD ──────────────────────────────────────────────────────────────────────

class TestFoodCRUD:

    def test_create_returns_parsed_entry(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "_parse_food", _mock_parse)
        r = client.post("/api/food", json={"raw_input": "I ate a donut"})
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "I ate a donut"
        assert data["category"] == "food"
        assert data["meal_type"] == "snack"
        assert data["quality"] == 3
        assert "id" in data
        assert "consumed_at" in data

    def test_create_missing_raw_input_returns_422(self, client):
        r = client.post("/api/food", json={})
        assert r.status_code == 422

    def test_create_blank_raw_input_returns_422(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "_parse_food", _mock_parse)
        r = client.post("/api/food", json={"raw_input": "   "})
        assert r.status_code == 422

    def test_delete_removes_entry(self, client, db_session, monkeypatch):
        monkeypatch.setattr(food_router, "_parse_food", _mock_parse)
        created = client.post("/api/food", json={"raw_input": "coffee"}).json()
        r = client.delete(f"/api/food/{created['id']}")
        assert r.status_code == 200
        assert db_session.query(models.FoodEntry).count() == 0

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/api/food/9999")
        assert r.status_code == 404


# ── Date filtering ────────────────────────────────────────────────────────────

LOCAL_DATE = "2026-06-15"
HEADERS = {"X-Local-Date": LOCAL_DATE}


class TestFoodDateFiltering:

    def test_get_returns_entries_for_requested_date(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "_parse_food", _mock_parse)
        client.post("/api/food", json={"raw_input": "breakfast", "consumed_at": "2026-06-15T08:00:00"})
        client.post("/api/food", json={"raw_input": "lunch",     "consumed_at": "2026-06-15T12:30:00"})
        client.post("/api/food", json={"raw_input": "yesterday", "consumed_at": "2026-06-14T19:00:00"})

        r = client.get("/api/food?date_str=2026-06-15", headers=HEADERS)
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_get_excludes_other_dates(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "_parse_food", _mock_parse)
        client.post("/api/food", json={"raw_input": "coffee", "consumed_at": "2026-06-15T08:00:00"})

        r = client.get("/api/food?date_str=2026-06-20", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == []

    def test_entries_sorted_by_consumed_at(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "_parse_food", _mock_parse)
        client.post("/api/food", json={"raw_input": "b", "consumed_at": "2026-06-15T14:00:00"})
        client.post("/api/food", json={"raw_input": "a", "consumed_at": "2026-06-15T09:00:00"})
        client.post("/api/food", json={"raw_input": "c", "consumed_at": "2026-06-15T19:00:00"})

        r = client.get("/api/food?date_str=2026-06-15", headers=HEADERS)
        times = [e["consumed_at"] for e in r.json()]
        assert times == sorted(times)

    def test_consumed_at_from_client_determines_date(self, client, monkeypatch):
        """
        The client passes consumed_at so the server stores the entry under the
        correct local date. A user eating at 11pm in UTC-5 (= 4am UTC next day)
        must see that entry under their local date, not the UTC date.

        The fix: frontend always passes consumed_at; backend filters by that
        timestamp's date, not by datetime.now(utc).
        """
        monkeypatch.setattr(food_router, "_parse_food", _mock_parse)
        # Client sends 11pm local Jun 15 — UTC would be Jun 16
        r = client.post("/api/food", json={
            "raw_input": "late night snack",
            "consumed_at": "2026-06-15T23:00:00",  # local time
        })
        assert r.status_code == 201

        # Must appear under the LOCAL date (Jun 15), not UTC date (Jun 16)
        assert len(client.get("/api/food?date_str=2026-06-15", headers=HEADERS).json()) == 1
        assert client.get("/api/food?date_str=2026-06-16", headers=HEADERS).json() == []

    def test_invalid_date_returns_422(self, client):
        r = client.get("/api/food?date_str=not-a-date", headers=HEADERS)
        assert r.status_code == 422

    def test_no_entries_returns_empty_list(self, client):
        r = client.get("/api/food?date_str=2026-06-15", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == []
