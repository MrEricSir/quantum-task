"""
Unit tests for all backend logic that depends on "today's date".

After the X-Local-Date fix, the server reads the client's local date from a
header instead of calling date.today().  These tests verify that every affected
code path honours the header:

  • _local_date() helper — header parsing + fallback
  • _section_for_date() — pure section assignment helper
  • _fmt_time() — time formatting for briefing context
  • _compute_streak() — streak anchored to local date
  • Habit check / uncheck — completion stored against local date
  • GET /api/habits — completed_today and streak respect local date
  • GET /api/calendar-events — section assignment uses local date
  • GET /api/todos — _auto_migrate_sections uses local date
  • PUT /api/todos/{id} — recurring next-occurrence section uses local date
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import date, datetime, time, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base
from main import app, get_db, _local_date, _section_for_date, _fmt_time, _compute_streak

# ── In-memory test database ───────────────────────────────────────────────────

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
    with TestingSessionLocal() as db:
        for name, color in [("personal", "#8b5cf6"), ("work", "#3b82f6")]:
            if not db.query(models.Tag).filter_by(name=name).first():
                db.add(models.Tag(name=name, color=color))
        db.commit()
    yield
    models.Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def get_tag_id(client, name):
    return next(t["id"] for t in client.get("/api/tags").json() if t["name"] == name)


LOCAL_DATE = {"X-Local-Date": "2026-06-04"}


# ── Pure helpers ──────────────────────────────────────────────────────────────

class TestSectionForDate:
    """_section_for_date maps a future date to today/week/month/later."""

    def _today(self):
        return date(2026, 6, 4)

    def test_same_day_is_today(self):
        assert _section_for_date(date(2026, 6, 4), self._today()) == "today"

    def test_yesterday_is_today(self):
        # Overdue → bumped to today
        assert _section_for_date(date(2026, 6, 3), self._today()) == "today"

    def test_tomorrow_is_week(self):
        assert _section_for_date(date(2026, 6, 5), self._today()) == "week"

    def test_seven_days_out_is_week(self):
        assert _section_for_date(date(2026, 6, 11), self._today()) == "week"

    def test_eight_days_out_is_month(self):
        assert _section_for_date(date(2026, 6, 12), self._today()) == "month"

    def test_thirty_days_out_is_month(self):
        assert _section_for_date(date(2026, 7, 4), self._today()) == "month"

    def test_thirty_one_days_out_is_later(self):
        assert _section_for_date(date(2026, 7, 5), self._today()) == "later"


class TestFmtTime:
    """_fmt_time formats a datetime's wall-clock time as a human string."""

    def test_noon(self):
        assert _fmt_time(datetime(2026, 6, 4, 12, 0)) == "12:00 PM"

    def test_midnight(self):
        assert _fmt_time(datetime(2026, 6, 4, 0, 0)) == "12:00 AM"

    def test_9am_no_leading_zero(self):
        result = _fmt_time(datetime(2026, 6, 4, 9, 0))
        assert result == "9:00 AM"

    def test_2_30_pm(self):
        assert _fmt_time(datetime(2026, 6, 4, 14, 30)) == "2:30 PM"

    def test_utc_aware_datetime(self):
        # Aware datetimes format the wall-clock UTC hour (correct for briefing context)
        dt = datetime(2026, 6, 4, 18, 0, tzinfo=timezone.utc)
        assert _fmt_time(dt) == "6:00 PM"


# ── _local_date header ────────────────────────────────────────────────────────

class TestLocalDateHeader:
    """X-Local-Date header controls the date used throughout the request."""

    def test_header_sets_calendar_section(self, client):
        """Calendar event section changes depending on the client's local date."""
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        # Fixed event: Sunday 2026-06-07 at noon UTC
        event_date = date(2026, 6, 7)
        mock_event = {
            "id": "hdr-test",
            "title": "Sunday event",
            "description": None,
            "start": datetime.combine(event_date, time(12, 0), tzinfo=timezone.utc),
            "end": datetime.combine(event_date, time(13, 0), tzinfo=timezone.utc),
            "all_day": False,
        }
        with patch("gcal.fetch_events", return_value=[mock_event]):
            # Three days before → "week"
            res_week = client.get("/api/calendar-events",
                                  headers={"X-Local-Date": "2026-06-04"})
            # On the same day → "today"
            res_today = client.get("/api/calendar-events",
                                   headers={"X-Local-Date": "2026-06-07"})

        assert res_week.status_code == 200
        assert res_today.status_code == 200
        assert res_week.json()[0]["section"] == "week"
        assert res_today.json()[0]["section"] == "today"

    def test_missing_header_does_not_500(self, client):
        """Requests without X-Local-Date still work (fallback to server date)."""
        res = client.get("/api/calendar-events")
        assert res.status_code == 200

    def test_invalid_header_does_not_500(self, client):
        """Malformed X-Local-Date falls back gracefully."""
        res = client.get("/api/calendar-events", headers={"X-Local-Date": "not-a-date"})
        assert res.status_code == 200


# ── Habit completion with local date ─────────────────────────────────────────

class TestHabitLocalDate:
    """completed_today and streak are anchored to X-Local-Date, not server UTC."""

    def _create_habit(self, client) -> int:
        res = client.post("/api/habits",
                          json={"name": "Morning run"},
                          headers=LOCAL_DATE)
        assert res.status_code == 201
        return res.json()["id"]

    def test_check_with_local_date_shows_completed_today(self, client):
        hid = self._create_habit(client)
        client.post(f"/api/habits/{hid}/check", headers=LOCAL_DATE)

        habits = client.get("/api/habits", headers=LOCAL_DATE).json()
        habit = next(h for h in habits if h["id"] == hid)
        assert habit["completed_today"] is True

    def test_completed_today_false_on_different_date(self, client):
        hid = self._create_habit(client)
        # Check on June 4
        client.post(f"/api/habits/{hid}/check", headers=LOCAL_DATE)

        # Fetch with June 5 — a different day → should NOT appear completed
        tomorrow = {"X-Local-Date": "2026-06-05"}
        habits = client.get("/api/habits", headers=tomorrow).json()
        habit = next(h for h in habits if h["id"] == hid)
        assert habit["completed_today"] is False

    def test_uncheck_respects_local_date(self, client):
        hid = self._create_habit(client)
        # Check on June 4
        client.post(f"/api/habits/{hid}/check", headers=LOCAL_DATE)

        # Uncheck on June 5 — targets a different date, so June 4 record survives
        tomorrow = {"X-Local-Date": "2026-06-05"}
        client.delete(f"/api/habits/{hid}/check", headers=tomorrow)

        # June 4 completion is still there
        habits = client.get("/api/habits", headers=LOCAL_DATE).json()
        habit = next(h for h in habits if h["id"] == hid)
        assert habit["completed_today"] is True

    def test_uncheck_removes_correct_date_record(self, client):
        hid = self._create_habit(client)
        client.post(f"/api/habits/{hid}/check", headers=LOCAL_DATE)

        # Uncheck on same date → record is removed
        client.delete(f"/api/habits/{hid}/check", headers=LOCAL_DATE)

        habits = client.get("/api/habits", headers=LOCAL_DATE).json()
        habit = next(h for h in habits if h["id"] == hid)
        assert habit["completed_today"] is False

    def test_double_check_is_idempotent(self, client):
        hid = self._create_habit(client)
        client.post(f"/api/habits/{hid}/check", headers=LOCAL_DATE)
        client.post(f"/api/habits/{hid}/check", headers=LOCAL_DATE)

        habits = client.get("/api/habits", headers=LOCAL_DATE).json()
        habit = next(h for h in habits if h["id"] == hid)
        assert habit["completed_today"] is True


# ── Streak computation ────────────────────────────────────────────────────────

class TestStreakComputation:
    """_compute_streak counts consecutive days ending on 'today'."""

    def _create_habit(self, client) -> int:
        res = client.post("/api/habits", json={"name": "Streak habit"}, headers=LOCAL_DATE)
        assert res.status_code == 201
        return res.json()["id"]

    def _add_completion(self, habit_id: int, date_str: str):
        with TestingSessionLocal() as db:
            if not db.query(models.HabitCompletion).filter_by(
                habit_id=habit_id, date=date_str
            ).first():
                db.add(models.HabitCompletion(habit_id=habit_id, date=date_str))
                db.commit()

    def test_no_completions_streak_is_zero(self, client):
        hid = self._create_habit(client)
        habits = client.get("/api/habits", headers=LOCAL_DATE).json()
        assert next(h for h in habits if h["id"] == hid)["streak"] == 0

    def test_single_day_streak(self, client):
        hid = self._create_habit(client)
        self._add_completion(hid, "2026-06-04")
        habits = client.get("/api/habits", headers=LOCAL_DATE).json()
        assert next(h for h in habits if h["id"] == hid)["streak"] == 1

    def test_consecutive_days_counted(self, client):
        hid = self._create_habit(client)
        for d in ["2026-06-02", "2026-06-03", "2026-06-04"]:
            self._add_completion(hid, d)
        habits = client.get("/api/habits", headers=LOCAL_DATE).json()
        assert next(h for h in habits if h["id"] == hid)["streak"] == 3

    def test_gap_resets_streak(self, client):
        hid = self._create_habit(client)
        # June 02 and 04 with a gap on June 03
        for d in ["2026-06-02", "2026-06-04"]:
            self._add_completion(hid, d)
        habits = client.get("/api/habits", headers=LOCAL_DATE).json()
        # Only June 04 is unbroken (today); June 02 is isolated
        assert next(h for h in habits if h["id"] == hid)["streak"] == 1

    def test_streak_yesterday_no_today(self, client):
        hid = self._create_habit(client)
        # Completed up to yesterday but not today
        for d in ["2026-06-02", "2026-06-03"]:
            self._add_completion(hid, d)
        habits = client.get("/api/habits", headers=LOCAL_DATE).json()
        # Streak looks back from yesterday since today is not done
        assert next(h for h in habits if h["id"] == hid)["streak"] == 2

    def test_streak_anchored_to_header_date(self, client):
        hid = self._create_habit(client)
        for d in ["2026-06-04", "2026-06-05", "2026-06-06"]:
            self._add_completion(hid, d)

        # With X-Local-Date: 2026-06-06 → streak=3
        res_3 = client.get("/api/habits", headers={"X-Local-Date": "2026-06-06"}).json()
        assert next(h for h in res_3 if h["id"] == hid)["streak"] == 3

        # With X-Local-Date: 2026-06-04 → streak=1 (only June 04 counts)
        res_1 = client.get("/api/habits", headers={"X-Local-Date": "2026-06-04"}).json()
        assert next(h for h in res_1 if h["id"] == hid)["streak"] == 1


# ── Todo section auto-advance ─────────────────────────────────────────────────

class TestAutoMigrateSections:
    """_auto_migrate_sections moves scheduled todos forward using X-Local-Date."""

    def test_future_todo_stays_in_week(self, client):
        # scheduled for June 7 (3 days out from June 4) → "week"
        client.post("/api/todos", json={
            "title": "Future task",
            "section": "week",
            "scheduled_at": "2026-06-07T10:00:00",
        })
        todos = client.get("/api/todos", headers={"X-Local-Date": "2026-06-04"}).json()
        task = next(t for t in todos if t["title"] == "Future task")
        assert task["section"] == "week"

    def test_scheduled_date_advances_to_today(self, client):
        # Create in "week" section with scheduled_at = June 7
        client.post("/api/todos", json={
            "title": "Due today task",
            "section": "week",
            "scheduled_at": "2026-06-07T10:00:00",
        })
        # Fetch with local date = June 7 → auto-migrates to "today"
        todos = client.get("/api/todos", headers={"X-Local-Date": "2026-06-07"}).json()
        task = next(t for t in todos if t["title"] == "Due today task")
        assert task["section"] == "today"

    def test_overdue_todo_advances_to_today(self, client):
        client.post("/api/todos", json={
            "title": "Overdue task",
            "section": "week",
            "scheduled_at": "2026-06-04T10:00:00",
        })
        # Fetching on June 6 (2 days later) → still "today" (overdue is treated as today)
        todos = client.get("/api/todos", headers={"X-Local-Date": "2026-06-06"}).json()
        task = next(t for t in todos if t["title"] == "Overdue task")
        assert task["section"] == "today"

    def test_section_never_moves_backward(self, client):
        # "today" section task with scheduled_at in the future should not be demoted
        client.post("/api/todos", json={
            "title": "Manually placed today",
            "section": "today",
            "scheduled_at": "2026-06-10T10:00:00",
        })
        todos = client.get("/api/todos", headers={"X-Local-Date": "2026-06-04"}).json()
        task = next(t for t in todos if t["title"] == "Manually placed today")
        # Still "today" — forward-only migration never pushes to a later section
        assert task["section"] == "today"


# ── Recurring todo next occurrence ────────────────────────────────────────────

class TestRecurringTodoSection:
    """When a recurring todo is completed, the next occurrence uses X-Local-Date."""

    def test_next_occurrence_in_week_section(self, client):
        # Daily recurring todo scheduled for June 4
        create_res = client.post("/api/todos", json={
            "title": "Daily standup",
            "section": "today",
            "scheduled_at": "2026-06-04T09:00:00",
            "recurrence_rule": "daily",
        })
        todo_id = create_res.json()["id"]

        # Complete it on June 4 → next occurrence is June 5 (1 day out → "week")
        client.put(f"/api/todos/{todo_id}",
                   json={"completed": True},
                   headers={"X-Local-Date": "2026-06-04"})

        todos = client.get("/api/todos", headers={"X-Local-Date": "2026-06-04"}).json()
        next_todo = next(
            (t for t in todos if t["title"] == "Daily standup" and not t["completed"]),
            None,
        )
        assert next_todo is not None, "Next occurrence should be created"
        assert next_todo["section"] == "week"

    def test_next_occurrence_section_uses_local_date(self, client):
        # Weekly recurring todo scheduled for June 4
        create_res = client.post("/api/todos", json={
            "title": "Weekly review",
            "section": "today",
            "scheduled_at": "2026-06-04T10:00:00",
            "recurrence_rule": "weekly",
        })
        todo_id = create_res.json()["id"]

        # Complete on June 4 → next occurrence is June 11 (7 days out)
        # From June 4's perspective: delta=7 → "week"
        client.put(f"/api/todos/{todo_id}",
                   json={"completed": True},
                   headers={"X-Local-Date": "2026-06-04"})

        todos = client.get("/api/todos", headers={"X-Local-Date": "2026-06-04"}).json()
        next_todo = next(
            (t for t in todos if t["title"] == "Weekly review" and not t["completed"]),
            None,
        )
        assert next_todo is not None
        # June 11 is exactly 7 days from June 4 → "week"
        assert next_todo["section"] == "week"
