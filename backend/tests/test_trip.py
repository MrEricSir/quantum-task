"""
Tests for trip/ -- trip CRUD (routers/trip.py's router) and retrospective content
generation (trip/generate.py's generate_trip_retrospective).

No dedicated test file existed before -- this is all new coverage for a new feature.
See PRODUCT_NOTES.md's "Trip mode" entry and streak.py's own trip-awareness tests in
tests/test_streak.py (that file owns streak-correctness; this one owns the trip
lifecycle + retrospective content).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
import models
from main import app
from deps import get_db

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


def _fake_llm_client(text):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


def _configure_telegram(db):
    import app_setting_keys as keys
    db.add(models.AppSetting(key=keys.TELEGRAM_BOT_TOKEN, value="tok"))
    db.add(models.AppSetting(key=keys.TELEGRAM_CHAT_ID, value="123"))
    db.commit()


class TestGetTrip:
    def test_no_trips_returns_null(self, client):
        res = client.get("/api/trip")
        assert res.status_code == 200
        assert res.json() is None

    def test_returns_active_trip(self, client):
        with TestingSessionLocal() as db:
            db.add(models.Trip(name="Tokyo", start_date="2026-09-01"))
            db.commit()
        res = client.get("/api/trip")
        assert res.status_code == 200
        assert res.json()["name"] == "Tokyo"
        assert res.json()["end_date"] is None

    def test_returns_most_recently_ended_trip_when_none_active(self, client):
        with TestingSessionLocal() as db:
            db.add(models.Trip(name="Old", start_date="2026-01-01", end_date="2026-01-05"))
            db.add(models.Trip(name="Recent", start_date="2026-02-01", end_date="2026-02-05"))
            db.commit()
        res = client.get("/api/trip")
        assert res.status_code == 200
        assert res.json()["name"] == "Recent"


class TestStartTrip:
    def test_starts_with_default_start_date_from_header(self, client):
        res = client.post("/api/trip", json={}, headers={"X-Local-Date": "2026-09-02"})
        assert res.status_code == 200
        body = res.json()
        assert body["start_date"] == "2026-09-02"
        assert body["end_date"] is None
        assert body["name"] is None

    def test_starts_with_custom_name_and_date(self, client):
        res = client.post("/api/trip", json={"name": "Tokyo", "start_date": "2026-09-01"})
        assert res.status_code == 200
        body = res.json()
        assert body["name"] == "Tokyo"
        assert body["start_date"] == "2026-09-01"

    def test_400_when_a_trip_is_already_active(self, client):
        client.post("/api/trip", json={"start_date": "2026-09-01"})
        res = client.post("/api/trip", json={"start_date": "2026-09-05"})
        assert res.status_code == 400

    def test_triggers_a_streak_recompute(self, client):
        """Creating (and closing) a trip over an existing gap should retroactively
        protect a streak, end to end through the router -- not just via streak.py
        directly (see tests/test_streak.py for the algorithm-level coverage)."""
        with TestingSessionLocal() as db:
            h = models.Habit(name="Walk")
            db.add(h)
            db.flush()
            habit_id = h.id
            db.add(models.HabitCompletion(habit_id=habit_id, date="2026-08-30"))
            db.add(models.HabitCompletion(habit_id=habit_id, date="2026-09-05"))
            db.commit()

        # Baseline, no trip yet: the gap (8/31-9/4) breaks the streak -- 9/5 reads as 1.
        with TestingSessionLocal() as db:
            from streak import recompute_all
            recompute_all(db, habit_id, today=date(2026, 9, 5))
            db.commit()
            entry = db.query(models.HabitStreakDay).filter_by(
                habit_id=habit_id, date="2026-09-05"
            ).first()
            assert entry.streak == 1

        # Create and close a trip covering exactly the gap (not 9/5 itself).
        start = client.post(
            "/api/trip", json={"start_date": "2026-08-31"}, headers={"X-Local-Date": "2026-09-01"}
        ).json()
        client.post(f"/api/trip/{start['id']}/end", headers={"X-Local-Date": "2026-09-04"})
        # That end call's own recompute ran with today capped at 9/4 -- confirm 9/5 picks
        # up the protection once anything touches "today" again (a no-op edit here, a real
        # habit check-off in production).
        client.put(f"/api/trip/{start['id']}", json={}, headers={"X-Local-Date": "2026-09-05"})

        with TestingSessionLocal() as db:
            entry = db.query(models.HabitStreakDay).filter_by(
                habit_id=habit_id, date="2026-09-05"
            ).first()
            assert entry is not None and entry.streak == 2


class TestUpdateTrip:
    def test_edits_name_and_start_date(self, client):
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        res = client.put(f"/api/trip/{start['id']}", json={"name": "Tokyo", "start_date": "2026-08-30"})
        assert res.status_code == 200
        assert res.json()["name"] == "Tokyo"
        assert res.json()["start_date"] == "2026-08-30"

    def test_404_for_missing_trip(self, client):
        res = client.put("/api/trip/999", json={"name": "x"})
        assert res.status_code == 404


def _backdate_trip_created_at(trip_id, minutes):
    """Push a trip's created_at into the past so it clears MIN_TRIP_DURATION_MINUTES --
    real tests can't wait an hour, and mocking datetime.now() would also freeze `today`
    (local_date), which most of these tests separately control via X-Local-Date."""
    with TestingSessionLocal() as db:
        trip = db.query(models.Trip).filter(models.Trip.id == trip_id).first()
        trip.created_at = datetime.utcnow() - timedelta(minutes=minutes)
        db.commit()


class TestEndTrip:
    def test_sets_end_date(self, client):
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        _backdate_trip_created_at(start["id"], 90)
        res = client.post(f"/api/trip/{start['id']}/end", headers={"X-Local-Date": "2026-09-06"})
        assert res.status_code == 200
        assert res.json()["trip"]["end_date"] == "2026-09-06"

    def test_400_when_already_ended(self, client):
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        client.post(f"/api/trip/{start['id']}/end")
        res = client.post(f"/api/trip/{start['id']}/end")
        assert res.status_code == 400

    def test_404_for_missing_trip(self, client):
        res = client.post("/api/trip/999/end")
        assert res.status_code == 404

    def test_sends_retrospective_and_marks_sent_when_telegram_configured(self, client):
        with TestingSessionLocal() as db:
            _configure_telegram(db)
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        _backdate_trip_created_at(start["id"], 90)

        with patch("trip.router.generate_trip_retrospective", return_value="<b>Welcome back</b>\n\nGreat trip.") as mock_gen, \
             patch("trip.router.send_message", return_value=True) as mock_send:
            res = client.post(f"/api/trip/{start['id']}/end", headers={"X-Local-Date": "2026-09-06"})

        assert res.status_code == 200
        body = res.json()
        assert body["retrospective"] == "<b>Welcome back</b>\n\nGreat trip."
        assert body["trip"]["retrospective_sent"] is True
        mock_gen.assert_called_once()
        mock_send.assert_called_once()

    def test_no_send_attempt_when_telegram_not_configured(self, client):
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        _backdate_trip_created_at(start["id"], 90)
        with patch("trip.router.send_message") as mock_send:
            res = client.post(f"/api/trip/{start['id']}/end")
        assert res.status_code == 200
        assert res.json()["retrospective"] is None
        assert res.json()["trip"]["retrospective_sent"] is False
        mock_send.assert_not_called()

    def test_failed_send_leaves_retrospective_sent_false_for_the_scheduler_backstop(self, client):
        with TestingSessionLocal() as db:
            _configure_telegram(db)
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        _backdate_trip_created_at(start["id"], 90)
        with patch("trip.router.generate_trip_retrospective", return_value="text"), \
             patch("trip.router.send_message", return_value=False):
            res = client.post(f"/api/trip/{start['id']}/end")
        assert res.json()["trip"]["retrospective_sent"] is False


class TestMinTripDuration:
    """Ending a trip too soon after starting it (an accidental toggle) shouldn't generate
    or send a retrospective -- see trip/router.py's MIN_TRIP_DURATION_MINUTES (60)."""

    def test_ending_immediately_skips_the_retrospective(self, client):
        with TestingSessionLocal() as db:
            _configure_telegram(db)
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()

        with patch("trip.router.generate_trip_retrospective") as mock_gen, \
             patch("trip.router.send_message") as mock_send:
            res = client.post(f"/api/trip/{start['id']}/end")

        assert res.status_code == 200
        body = res.json()
        assert body["retrospective"] is None
        assert body["trip"]["retrospective_sent"] is False
        assert body["trip"]["retrospective_skipped"] is True
        mock_gen.assert_not_called()
        mock_send.assert_not_called()

    def test_ending_just_under_the_threshold_still_skips(self, client):
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        _backdate_trip_created_at(start["id"], 59)
        res = client.post(f"/api/trip/{start['id']}/end")
        assert res.json()["trip"]["retrospective_skipped"] is True

    def test_ending_just_over_the_threshold_sends_normally(self, client):
        with TestingSessionLocal() as db:
            _configure_telegram(db)
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        _backdate_trip_created_at(start["id"], 61)
        with patch("trip.router.generate_trip_retrospective", return_value="text"), \
             patch("trip.router.send_message", return_value=True):
            res = client.post(f"/api/trip/{start['id']}/end")
        body = res.json()
        assert body["retrospective"] == "text"
        assert body["trip"]["retrospective_skipped"] is False
        assert body["trip"]["retrospective_sent"] is True

    def test_trip_still_ends_and_recomputes_even_when_skipped(self, client):
        """The toggle-off must still take effect for streak purposes -- only the
        retrospective is suppressed, not the trip actually ending."""
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        res = client.post(f"/api/trip/{start['id']}/end", headers={"X-Local-Date": "2026-09-01"})
        assert res.json()["trip"]["end_date"] == "2026-09-01"
        # A new trip can be started again -- confirms the old one is really closed.
        res2 = client.post("/api/trip", json={"start_date": "2026-09-02"})
        assert res2.status_code == 200


class TestDeleteTrip:
    def test_deletes_and_recomputes(self, client):
        start = client.post("/api/trip", json={"start_date": "2026-09-01"}).json()
        res = client.delete(f"/api/trip/{start['id']}")
        assert res.status_code == 200
        assert client.get("/api/trip").json() is None

    def test_404_for_missing_trip(self, client):
        res = client.delete("/api/trip/999")
        assert res.status_code == 404


class TestGenerateTripRetrospective:
    def test_includes_task_and_habit_counts_scoped_to_trip_window(self):
        with TestingSessionLocal() as db:
            db.add(models.Card(
                title="Book flight", completed=True,
                completed_at=datetime(2026, 9, 3, 12, tzinfo=timezone.utc).replace(tzinfo=None),
            ))
            db.add(models.Card(
                title="Unrelated, outside window", completed=True,
                completed_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc).replace(tzinfo=None),
            ))
            h = models.Habit(name="Journal")
            db.add(h)
            db.flush()
            db.add(models.HabitCompletion(habit_id=h.id, date="2026-09-02"))
            db.commit()

        from trip.generate import generate_trip_retrospective
        fake_client = _fake_llm_client("Great trip!")
        with patch("trip.generate.SessionLocal", TestingSessionLocal), \
             patch("deps.llm_client", return_value=fake_client):
            text = generate_trip_retrospective("2026-09-01", "2026-09-06", "Tokyo", 0)

        assert text is not None
        assert "Welcome back from Tokyo" in text
        assert "Great trip!" in text
        user_content = fake_client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "Tasks completed while away: 1" in user_content
        assert "Journal: checked off 1 day" in user_content
        assert "not broken" in user_content or "were preserved" in user_content

    def test_no_habit_activity_still_reassures_streaks_are_safe(self):
        from trip.generate import generate_trip_retrospective
        fake_client = _fake_llm_client("Welcome back!")
        with patch("trip.generate.SessionLocal", TestingSessionLocal), \
             patch("deps.llm_client", return_value=fake_client):
            generate_trip_retrospective("2026-09-01", "2026-09-06", None, 0)
        user_content = fake_client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "none of the streaks were broken" in user_content

    def test_returns_none_on_llm_failure(self):
        from trip.generate import generate_trip_retrospective
        with patch("trip.generate.SessionLocal", TestingSessionLocal), \
             patch("deps.llm_client", side_effect=RuntimeError("LLM down")):
            text = generate_trip_retrospective("2026-09-01", "2026-09-06", None, 0)
        assert text is None

    def test_health_data_counts_included(self):
        with TestingSessionLocal() as db:
            db.add(models.WithingsMeasurement(date="2026-09-02", metric="steps", value=8000))
            db.add(models.FoodEntry(
                raw_input="coffee", name="coffee", category="drink",
                consumed_at=datetime(2026, 9, 3, 9),
            ))
            db.add(models.WorkoutEntry(
                raw_input="ran 2mi", type="run", value=2, unit="mi",
                logged_at=datetime(2026, 9, 4, 8),
            ))
            db.commit()
        from trip.generate import generate_trip_retrospective
        fake_client = _fake_llm_client("Nice.")
        with patch("trip.generate.SessionLocal", TestingSessionLocal), \
             patch("deps.llm_client", return_value=fake_client):
            generate_trip_retrospective("2026-09-01", "2026-09-06", None, 0)
        user_content = fake_client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "1 measurement" in user_content
        assert "1 food log" in user_content
        assert "1 workout" in user_content
