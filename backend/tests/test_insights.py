"""
Tests for routers/insights.py's _generate_texts -- previously had zero test coverage
anywhere in the codebase. Scoped to the LLM-reliability/error-handling fix applied here
(reasoning_effort, and logging a failure instead of silently swallowing it) -- not a full
audit of insights.py.

TestCompletionTimeInsight covers a separate, later fix: _completion_time_insight bucketed
Card.completed_at (a UTC-instant column, per models.py's own documented convention) by its
raw hour, never converting to the client's local time -- so the morning/afternoon/evening
label was computed from the wrong hours for any user not at UTC+0.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import routers.insights as insights
from main import app
from deps import get_db
from routers.insights import _completion_time_insight, _generate_texts


@pytest.fixture(autouse=True)
def clear_text_cache():
    insights._text_cache.clear()
    yield
    insights._text_cache.clear()


def _mock_llm(payload):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class TestGenerateTexts:

    def test_returns_texts_matched_by_index(self):
        payload = {"insights": [{"index": 0, "text": "Custom text A"}, {"index": 1, "text": "Custom text B"}]}
        with patch("routers.insights.llm_client", return_value=_mock_llm(payload)):
            texts = _generate_texts(["pattern A", "pattern B"])

        assert texts == ["Custom text A", "Custom text B"]

    def test_requests_reasoning_effort_low(self):
        """The reliability fix: on a reasoning model, an unbounded chain-of-thought can burn
        the whole max_tokens budget before the real JSON answer, truncating it -- see
        correlations.py's _generate_experiment for the full story."""
        mock_client = _mock_llm({"insights": []})
        with patch("routers.insights.llm_client", return_value=mock_client), \
             patch("deps.LLM_MODEL", "openai/gpt-oss-120b"):
            _generate_texts(["pattern A"])

        assert mock_client.chat.completions.create.call_args.kwargs["reasoning_effort"] == "low"

    def test_missing_index_falls_back_to_empty_string(self):
        payload = {"insights": [{"index": 0, "text": "Custom text A"}]}
        with patch("routers.insights.llm_client", return_value=_mock_llm(payload)):
            texts = _generate_texts(["pattern A", "pattern B"])

        assert texts == ["Custom text A", ""]

    def test_llm_failure_falls_back_to_empty_strings_and_is_logged(self, capsys):
        """Previously a bare `except Exception: texts = ["" for _ in patterns]` with zero
        logging -- callers already show an honest static template when text is empty, so
        this doesn't display anything false, but a real recurring failure was invisible."""
        broken_client = MagicMock()
        broken_client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch("routers.insights.llm_client", return_value=broken_client):
            texts = _generate_texts(["pattern A", "pattern B"])

        assert texts == ["", ""]
        assert "text generation error" in capsys.readouterr().out

    def test_malformed_json_falls_back_to_empty_strings_and_is_logged(self, capsys):
        malformed_client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content='{"insights": [truncated'))]
        malformed_client.chat.completions.create.return_value = resp
        with patch("routers.insights.llm_client", return_value=malformed_client):
            texts = _generate_texts(["pattern A"])

        assert texts == [""]
        assert "text generation error" in capsys.readouterr().out

    def test_repeated_call_with_same_patterns_uses_cache(self):
        mock_client = _mock_llm({"insights": [{"index": 0, "text": "Custom text"}]})
        with patch("routers.insights.llm_client", return_value=mock_client):
            _generate_texts(["pattern A"])
            _generate_texts(["pattern A"])

        assert mock_client.chat.completions.create.call_count == 1


# ── _completion_time_insight ────────────────────────────────────────────────────

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
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


def _add_completed_cards(db, utc_hours):
    """One completed Card per hour in utc_hours, all well within the 90-day window."""
    base_day = datetime.now(timezone.utc) - timedelta(days=1)
    for i, hour in enumerate(utc_hours):
        db.add(models.Card(
            title=f"Task {i}",
            completed=True,
            completed_at=base_day.replace(hour=hour, minute=0, second=0, microsecond=0),
        ))
    db.commit()


class TestCompletionTimeInsight:

    def test_returns_none_with_fewer_than_20_completions(self, db):
        _add_completed_cards(db, [20] * 19)
        assert _completion_time_insight(db, utc_offset_minutes=0) is None

    def test_zero_offset_buckets_by_raw_utc_hour(self, db):
        """Baseline/regression check: a UTC+0 client's local hour IS the raw UTC hour, so
        the fix must not change behavior for this case."""
        _add_completed_cards(db, [20] * 20)  # 20:00 UTC, 20x -> "evening" bucket
        result = _completion_time_insight(db, utc_offset_minutes=0)
        assert result["peak_window"] == "evening"

    def test_converts_to_local_time_before_bucketing(self, db):
        """The actual bug: 20:00 UTC reads as evening (18-23) if bucketed raw, but for a
        client 10 hours behind UTC (e.g. US Hawaii/UTC-10, offset=+600 per deps.py's
        utc_offset_minutes convention) it's 10:00 local -- morning, not evening."""
        _add_completed_cards(db, [20] * 20)
        result = _completion_time_insight(db, utc_offset_minutes=600)
        assert result["peak_window"] == "morning"

    def test_local_time_conversion_can_cross_midnight(self, db):
        """2:00 UTC is 21:00 (evening) the PREVIOUS local day for a client 5 hours behind
        UTC (US Eastern/UTC-5, offset=+300 per deps.py's convention) -- the naive
        subtraction must still land in the right bucket across the day boundary."""
        _add_completed_cards(db, [2] * 20)
        result = _completion_time_insight(db, utc_offset_minutes=300)
        assert result["peak_window"] == "evening"


# ── GET /api/insights -- stuck_task days_stuck (real endpoint) ─────────────────

def override_get_db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    insights._response_cache.clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    insights._response_cache.clear()


class TestStuckTaskDaysStuck:

    def test_days_stuck_uses_local_entry_date_not_raw_utc(self, db, client):
        """Regression test: entry_date used to be (today_since or created_at).date() --
        a raw UTC-instant .date() call with no offset conversion, the same bug class
        just fixed in _completion_time_insight. today_since=2026-07-20T15:00:00 (UTC) is
        2026-07-21 01:00 local for a client 10 hours ahead of UTC (offset=-600, e.g.
        Sydney) -- the raw UTC date (Jul 20) and the correct local date (Jul 21) give
        different days_stuck counts against a local "today" of 2026-08-19 (30 vs 29)."""
        db.add(models.Card(
            title="Stuck task", section="today", completed=False, archived=False,
            today_since=datetime(2026, 7, 20, 15, 0),
        ))
        db.commit()

        with patch("routers.insights.llm_client", side_effect=RuntimeError("no LLM in test")):
            res = client.get("/api/insights", headers={
                "X-Local-Date": "2026-08-19", "X-UTC-Offset": "-600",
            })

        assert res.status_code == 200
        stuck = [i for i in res.json() if i["type"] == "stuck_task"]
        assert len(stuck) == 1
        assert stuck[0]["days_stuck"] == 29
