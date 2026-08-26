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
# create_food_entry() calls capabilities.food.parse_food_entries(), imported
# into routers/food.py as `parse_food_entries` -- mock that name, same as
# tests/test_telegram.py mocks it on the Telegram side, so both suites cover
# their own glue code around the one shared split+enrich implementation.

_PARSED = {
    "name": "donut",
    "category": "food",
    "source_text": None,
    "notes": "High in sugar and refined carbs.",
    "quality": 3,
    "calories": 300,
}


def _mock_parse(raw: str) -> list[dict]:
    return [{**_PARSED, "name": raw[:20]}]  # name reflects input for traceability


def _mock_parse_multi(*names):
    return lambda raw: [{**_PARSED, "name": n, "source_text": n} for n in names]


# ── CRUD ──────────────────────────────────────────────────────────────────────

class TestFoodCRUD:

    def test_create_returns_parsed_entry(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "parse_food_entries", _mock_parse)
        r = client.post("/api/food", json={"raw_input": "I ate a donut"})
        assert r.status_code == 201
        data = r.json()
        assert len(data) == 1
        entry = data[0]
        assert entry["name"] == "I ate a donut"
        assert entry["category"] == "food"
        assert entry["quality"] == 3
        assert "id" in entry
        assert "consumed_at" in entry

    def test_splits_a_multi_item_message_into_separate_entries(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "parse_food_entries",
                             _mock_parse_multi("Bagel", "Coffee", "Banana"))
        r = client.post("/api/food", json={"raw_input": "had a bagel, coffee, and a banana"})
        assert r.status_code == 201
        data = r.json()
        assert len(data) == 3
        assert {e["name"] for e in data} == {"Bagel", "Coffee", "Banana"}
        assert {e["raw_input"] for e in data} == {"Bagel", "Coffee", "Banana"}

    def test_create_missing_raw_input_returns_422(self, client):
        r = client.post("/api/food", json={})
        assert r.status_code == 422

    def test_create_blank_raw_input_returns_422(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "parse_food_entries", _mock_parse)
        r = client.post("/api/food", json={"raw_input": "   "})
        assert r.status_code == 422

    def test_delete_removes_entry(self, client, db_session, monkeypatch):
        monkeypatch.setattr(food_router, "parse_food_entries", _mock_parse)
        created = client.post("/api/food", json={"raw_input": "coffee"}).json()
        r = client.delete(f"/api/food/{created[0]['id']}")
        assert r.status_code == 200
        assert db_session.query(models.FoodEntry).count() == 0

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/api/food/9999")
        assert r.status_code == 404


# ── Update (manual correction) ───────────────────────────────────────────────

class TestFoodUpdate:

    def _create(self, client, monkeypatch, raw_input="coffee"):
        monkeypatch.setattr(food_router, "parse_food_entries", _mock_parse)
        return client.post("/api/food", json={"raw_input": raw_input}).json()[0]

    def test_updates_name(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/food/{entry['id']}", json={"name": "Oat milk latte"})
        assert r.status_code == 200
        assert r.json()["name"] == "Oat milk latte"

    def test_updates_calories_and_quality(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/food/{entry['id']}", json={"calories": 120, "quality": 6})
        assert r.status_code == 200
        data = r.json()
        assert data["calories"] == 120
        assert data["quality"] == 6

    def test_clamps_quality_to_valid_range(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/food/{entry['id']}", json={"quality": 99})
        assert r.json()["quality"] == 10

    def test_can_clear_calories_and_quality(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/food/{entry['id']}", json={"calories": None, "quality": None})
        data = r.json()
        assert data["calories"] is None
        assert data["quality"] is None

    def test_updates_notes(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/food/{entry['id']}", json={"notes": "Actually decaf."})
        assert r.json()["notes"] == "Actually decaf."

    def test_updates_consumed_at(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/food/{entry['id']}", json={"consumed_at": "2026-06-15T08:30:00"})
        assert r.status_code == 200
        assert r.json()["consumed_at"].startswith("2026-06-15T08:30:00")

    def test_invalid_consumed_at_returns_422(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/food/{entry['id']}", json={"consumed_at": "not-a-date"})
        assert r.status_code == 422

    def test_blank_name_returns_422(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        r = client.put(f"/api/food/{entry['id']}", json={"name": "   "})
        assert r.status_code == 422

    def test_partial_update_leaves_other_fields_untouched(self, client, monkeypatch):
        entry = self._create(client, monkeypatch)
        client.put(f"/api/food/{entry['id']}", json={"calories": 200})
        r = client.put(f"/api/food/{entry['id']}", json={"name": "Renamed"})
        data = r.json()
        assert data["name"] == "Renamed"
        assert data["calories"] == 200

    def test_update_nonexistent_returns_404(self, client):
        r = client.put("/api/food/9999", json={"name": "x"})
        assert r.status_code == 404


# ── Date filtering ────────────────────────────────────────────────────────────

LOCAL_DATE = "2026-06-15"
HEADERS = {"X-Local-Date": LOCAL_DATE}


class TestFoodDateFiltering:

    def test_get_returns_entries_for_requested_date(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "parse_food_entries", _mock_parse)
        client.post("/api/food", json={"raw_input": "breakfast", "consumed_at": "2026-06-15T08:00:00"})
        client.post("/api/food", json={"raw_input": "lunch",     "consumed_at": "2026-06-15T12:30:00"})
        client.post("/api/food", json={"raw_input": "yesterday", "consumed_at": "2026-06-14T19:00:00"})

        r = client.get("/api/food?date_str=2026-06-15", headers=HEADERS)
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_get_excludes_other_dates(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "parse_food_entries", _mock_parse)
        client.post("/api/food", json={"raw_input": "coffee", "consumed_at": "2026-06-15T08:00:00"})

        r = client.get("/api/food?date_str=2026-06-20", headers=HEADERS)
        assert r.status_code == 200
        assert r.json() == []

    def test_entries_sorted_by_consumed_at(self, client, monkeypatch):
        monkeypatch.setattr(food_router, "parse_food_entries", _mock_parse)
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
        monkeypatch.setattr(food_router, "parse_food_entries", _mock_parse)
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


# ── Quality trend ──────────────────────────────────────────────────────────────

class TestFoodQualityTrend:

    def _seed(self, db_session, consumed_at: str, quality: int):
        entry = models.FoodEntry(
            raw_input="test", name="test", category="food",
            quality=quality, calories=None, notes=None,
            consumed_at=datetime.fromisoformat(consumed_at),
        )
        db_session.add(entry)
        db_session.commit()

    def test_empty_returns_empty_list(self, client):
        r = client.get("/api/food/quality-trend?days=30")
        assert r.status_code == 200
        assert r.json() == []

    def test_averages_quality_by_day(self, client, db_session):
        self._seed(db_session, "2026-07-25T08:00:00", 2)
        self._seed(db_session, "2026-07-25T18:00:00", 4)
        r = client.get("/api/food/quality-trend?days=30")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["date"] == "2026-07-25"
        assert data[0]["value"] == 3.0

    def test_excludes_null_quality(self, client, db_session):
        entry = models.FoodEntry(
            raw_input="test", name="no quality", category="food",
            quality=None, calories=None, notes=None,
            consumed_at=datetime.fromisoformat("2026-07-25T10:00:00"),
        )
        db_session.add(entry)
        db_session.commit()
        r = client.get("/api/food/quality-trend?days=30")
        assert r.json() == []

    def test_respects_days_param(self, client, db_session):
        self._seed(db_session, "2026-06-01T10:00:00", 5)  # old entry
        self._seed(db_session, "2026-07-25T10:00:00", 3)  # recent entry
        r = client.get("/api/food/quality-trend?days=7")
        data = r.json()
        dates = [d["date"] for d in data]
        assert "2026-06-01" not in dates
        assert "2026-07-25" in dates

    def test_results_sorted_ascending(self, client, db_session):
        self._seed(db_session, "2026-07-25T10:00:00", 3)
        self._seed(db_session, "2026-07-23T10:00:00", 5)
        self._seed(db_session, "2026-07-24T10:00:00", 4)
        data = client.get("/api/food/quality-trend?days=30").json()
        dates = [d["date"] for d in data]
        assert dates == sorted(dates)

    def test_cutoff_uses_client_local_date_not_server_clock(self, client, db_session):
        """The trailing-window cutoff is anchored on X-Local-Date, not the
        server's clock -- a client several days behind/ahead of the server
        (or a server clock skew) must still get a cutoff based on their own
        local date."""
        self._seed(db_session, "2025-12-31T10:00:00", 5)  # just outside a 7-day window ending 2026-01-08
        self._seed(db_session, "2026-01-08T10:00:00", 3)  # inside that window
        r = client.get(
            "/api/food/quality-trend?days=7",
            headers={"X-Local-Date": "2026-01-08"},
        )
        dates = [d["date"] for d in r.json()]
        assert "2026-01-08" in dates
        assert "2025-12-31" not in dates


class TestParseFoodEntriesInternals:
    """Every other test in this file mocks parse_food_entries as a black box -- these test
    its own LLM call and error handling directly, previously with zero coverage anywhere."""

    def _mock_llm(self, content):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=content))]
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        return client

    def test_requests_reasoning_effort_low_and_json_mode(self):
        """The reliability fix: on a reasoning model, an unbounded chain-of-thought can
        burn the whole max_tokens budget before the real JSON answer, truncating it -- see
        correlations.py's _generate_experiment for the full story."""
        from unittest.mock import patch
        import capabilities.food as food_mod
        payload = '{"items": [{"name": "Coffee", "category": "drink", "source_text": "coffee", "notes": null, "quality": 7, "calories": 5}]}'
        mock_client = self._mock_llm(payload)
        with patch("deps.llm_client", return_value=mock_client), \
             patch("deps.LLM_MODEL", "openai/gpt-oss-120b"):
            food_mod.parse_food_entries("coffee")

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "low"
        assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_llm_failure_falls_back_to_raw_text_and_is_logged(self, capsys):
        """Previously a bare `except Exception:` with zero logging -- callers already get
        an honest fallback (the verbatim raw text as the entry name, no fabricated
        category/quality/calories), but a real recurring failure was invisible."""
        from unittest.mock import patch, MagicMock
        import capabilities.food as food_mod
        broken_client = MagicMock()
        broken_client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch("deps.llm_client", return_value=broken_client):
            result = food_mod.parse_food_entries("grilled chicken and rice")

        assert result == [{"name": "grilled chicken and rice", "category": "food",
                            "source_text": None, "notes": None, "quality": None, "calories": None}]
        assert "parse error" in capsys.readouterr().out

    def test_malformed_json_falls_back_to_raw_text_and_is_logged(self, capsys):
        from unittest.mock import patch
        import capabilities.food as food_mod
        mock_client = self._mock_llm('{"items": [truncated')  # malformed
        with patch("deps.llm_client", return_value=mock_client):
            result = food_mod.parse_food_entries("coffee")

        assert result[0]["name"] == "coffee"
        assert "parse error" in capsys.readouterr().out

