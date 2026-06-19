"""
Unit tests for the Google Calendar endpoints.

Uses FastAPI's TestClient with an in-memory SQLite database — no server required.
Calendar event fetching (GET /api/calendar-events) is tested with a mock so no
real iCal URL is needed.
"""

import pytest
from unittest.mock import patch
from datetime import datetime, date, time, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import icalendar as ical_lib
import gcal
import models
import schemas
from database import Base
from main import app
from deps import get_db

# ── In-memory test database ──────────────────────────────────────────────────
# StaticPool forces all connections to reuse one in-memory SQLite database,
# so tables created in setup are visible to every session in the test.

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
    # Seed default tags
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
    tags = client.get("/api/tags").json()
    return next(t["id"] for t in tags if t["name"] == name)


# ── GET /api/calendar-mappings ───────────────────────────────────────────────

class TestGetMappings:
    def test_empty_initially(self, client):
        res = client.get("/api/calendar-mappings")
        assert res.status_code == 200
        assert res.json() == []

    def test_returns_saved_mappings(self, client):
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        res = client.get("/api/calendar-mappings")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["tag_id"] == work_id
        assert data[0]["ical_url"] == "https://example.com/work.ics"


# ── PUT /api/calendar-mappings ───────────────────────────────────────────────

class TestPutMappings:
    def test_save_single_mapping(self, client):
        work_id = get_tag_id(client, "work")
        res = client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/cal.ics"}
        ])
        assert res.status_code == 200
        assert res.json() == {"ok": True}

    def test_save_multiple_mappings(self, client):
        work_id = get_tag_id(client, "work")
        personal_id = get_tag_id(client, "personal")
        res = client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"},
            {"tag_id": personal_id, "ical_url": "https://example.com/personal.ics"},
        ])
        assert res.status_code == 200
        data = client.get("/api/calendar-mappings").json()
        assert len(data) == 2

    def test_replaces_existing_mappings(self, client):
        work_id = get_tag_id(client, "work")
        personal_id = get_tag_id(client, "personal")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/old.ics"},
            {"tag_id": personal_id, "ical_url": "https://example.com/personal.ics"},
        ])
        # Replace with only work mapping
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/new.ics"}
        ])
        data = client.get("/api/calendar-mappings").json()
        assert len(data) == 1
        assert data[0]["ical_url"] == "https://example.com/new.ics"

    def test_clear_all_mappings(self, client):
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        client.put("/api/calendar-mappings", json=[])
        data = client.get("/api/calendar-mappings").json()
        assert data == []


# ── GET /api/calendar-events ─────────────────────────────────────────────────

class TestGetCalendarEvents:
    def test_returns_empty_with_no_mappings(self, client):
        res = client.get("/api/calendar-events")
        assert res.status_code == 200
        assert res.json() == []

    def test_assigns_today_section(self, client):
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        # Pin start to noon today (UTC-aware so it compares correctly with datetime.now(UTC)).
        # End is +1 day so it's never filtered as "already ended".
        mock_event = {
            "id": "evt-001",
            "title": "Team standup",
            "description": None,
            "start": datetime.combine(date.today(), time(12, 0), tzinfo=timezone.utc),
            "end": datetime.now(timezone.utc) + timedelta(days=1),
            "all_day": False,
        }
        with patch("gcal.fetch_events", return_value=[mock_event]):
            res = client.get("/api/calendar-events")
        assert res.status_code == 200
        events = res.json()
        assert len(events) == 1
        assert events[0]["title"] == "Team standup"
        assert events[0]["section"] == "today"

    def test_assigns_week_section(self, client):
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        in_3_days = date.today() + timedelta(days=3)
        mock_event = {
            "id": "evt-002",
            "title": "Sprint review",
            "description": None,
            "start": datetime.combine(in_3_days, datetime.min.time().replace(hour=14), tzinfo=timezone.utc),
            "end": None,
            "all_day": False,
        }
        with patch("gcal.fetch_events", return_value=[mock_event]):
            res = client.get("/api/calendar-events")
        assert res.status_code == 200
        assert res.json()[0]["section"] == "week"

    def test_assigns_month_section(self, client):
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        in_2_weeks = date.today() + timedelta(days=14)
        mock_event = {
            "id": "evt-003",
            "title": "Quarterly review",
            "description": None,
            "start": datetime.combine(in_2_weeks, datetime.min.time().replace(hour=10), tzinfo=timezone.utc),
            "end": None,
            "all_day": False,
        }
        with patch("gcal.fetch_events", return_value=[mock_event]):
            res = client.get("/api/calendar-events")
        assert res.status_code == 200
        assert res.json()[0]["section"] == "month"

    def test_excludes_past_events(self, client):
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        yesterday = date.today() - timedelta(days=1)
        mock_event = {
            "id": "evt-004",
            "title": "Yesterday's meeting",
            "description": None,
            "start": datetime.combine(yesterday, datetime.min.time().replace(hour=10), tzinfo=timezone.utc),
            "end": None,
            "all_day": False,
        }
        with patch("gcal.fetch_events", return_value=[mock_event]):
            res = client.get("/api/calendar-events")
        assert res.status_code == 200
        assert res.json() == []

    def test_attaches_tag_info(self, client):
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        today = date.today()
        mock_event = {
            "id": "evt-005",
            "title": "All hands",
            "description": None,
            "start": datetime.now(timezone.utc) + timedelta(hours=1),
            "end": None,
            "all_day": False,
        }
        with patch("gcal.fetch_events", return_value=[mock_event]):
            res = client.get("/api/calendar-events")
        event = res.json()[0]
        assert event["tag_id"] == work_id
        assert event["tag_name"] == "work"
        assert event["tag_color"] == "#3b82f6"

    def test_mixed_allday_and_timed_events_sort_without_error(self, client):
        """Sorting must not raise TypeError when all-day (naive) and timed (UTC-aware) events coexist."""
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        tomorrow = date.today() + timedelta(days=1)
        timed_event = {
            "id": "timed-1",
            "title": "Timed event",
            "description": None,
            "start": datetime.combine(tomorrow, time(10, 0), tzinfo=timezone.utc),
            "end": datetime.combine(tomorrow, time(11, 0), tzinfo=timezone.utc),
            "all_day": False,
        }
        allday_event = {
            "id": "allday-1",
            "title": "All day event",
            "description": None,
            "start": datetime.combine(tomorrow, time(0, 0)),  # naive, as gcal.py returns
            "end": None,
            "all_day": True,
        }
        with patch("gcal.fetch_events", return_value=[timed_event, allday_event]):
            res = client.get("/api/calendar-events")
        assert res.status_code == 200, f"Got 500: mixed naive/aware sort should not raise TypeError"
        titles = {e["title"] for e in res.json()}
        assert "Timed event" in titles
        assert "All day event" in titles

    def test_skips_failing_url_gracefully(self, client):
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/broken.ics"}
        ])
        with patch("gcal.fetch_events", side_effect=Exception("connection refused")):
            res = client.get("/api/calendar-events")
        # Should not 500 — returns empty list gracefully
        assert res.status_code == 200
        assert res.json() == []


# ── Inline ICS helpers ────────────────────────────────────────────────────────

class _MockResponse:
    """Minimal requests.Response stub for patching gcal.requests.get."""
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def _vcal(*vevents: str) -> bytes:
    """Wrap VEVENT strings in a VCALENDAR envelope with proper CRLF line endings."""
    all_lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//EN"]
    for ev in vevents:
        all_lines.extend(ev.split("\r\n"))
    all_lines.append("END:VCALENDAR")
    return "\r\n".join(all_lines).encode()


def _vevent(uid: str, summary: str, dtstart: str, *, status: str = "", sequence: int | None = None) -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{summary}",
        f"DTSTART:{dtstart}",
        "DTSTAMP:20260101T000000Z",
    ]
    if status:
        lines.append(f"STATUS:{status}")
    if sequence is not None:
        lines.append(f"SEQUENCE:{sequence}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def _vevent_allday(uid: str, summary: str, dt: date) -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{summary}",
        f"DTSTART;VALUE=DATE:{dt.strftime('%Y%m%d')}",
        "DTSTAMP:20260101T000000Z",
        "END:VEVENT",
    ]
    return "\r\n".join(lines)


def _vevent_with_desc(uid: str, summary: str, dtstart: str, description: str) -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{summary}",
        f"DTSTART:{dtstart}",
        f"DESCRIPTION:{description}",
        "DTSTAMP:20260101T000000Z",
        "END:VEVENT",
    ]
    return "\r\n".join(lines)


# ── Export token ──────────────────────────────────────────────────────────────

class TestExportToken:
    def test_get_token_returns_string(self, client):
        res = client.get("/api/settings/export-token")
        assert res.status_code == 200
        token = res.json().get("token")
        assert isinstance(token, str) and len(token) > 0

    def test_token_is_stable_across_requests(self, client):
        t1 = client.get("/api/settings/export-token").json()["token"]
        t2 = client.get("/api/settings/export-token").json()["token"]
        assert t1 == t2

    def test_rotate_returns_new_token(self, client):
        old = client.get("/api/settings/export-token").json()["token"]
        new = client.post("/api/settings/export-token/rotate").json()["token"]
        assert new != old

    def test_rotated_token_becomes_current(self, client):
        new = client.post("/api/settings/export-token/rotate").json()["token"]
        current = client.get("/api/settings/export-token").json()["token"]
        assert current == new


# ── iCal export endpoint ──────────────────────────────────────────────────────

class TestICalExport:
    def _token(self, client) -> str:
        return client.get("/api/settings/export-token").json()["token"]

    def test_missing_token_returns_422(self, client):
        res = client.get("/api/calendar/export.ics")
        assert res.status_code == 422

    def test_invalid_token_returns_401(self, client):
        res = client.get("/api/calendar/export.ics?token=notavalidtoken")
        assert res.status_code == 401

    def test_valid_token_returns_200(self, client):
        res = client.get(f"/api/calendar/export.ics?token={self._token(client)}")
        assert res.status_code == 200

    def test_content_type_is_calendar(self, client):
        res = client.get(f"/api/calendar/export.ics?token={self._token(client)}")
        assert "text/calendar" in res.headers["content-type"]

    def test_empty_export_is_valid_ics(self, client):
        res = client.get(f"/api/calendar/export.ics?token={self._token(client)}")
        cal = ical_lib.Calendar.from_ical(res.content)
        assert cal is not None

    def test_scheduled_todo_appears_in_export(self, client):
        client.post("/api/cards", json={
            "title": "Export test task",
            "section": "week",
            "scheduled_at": "2026-06-15T10:00:00",
        })
        res = client.get(f"/api/calendar/export.ics?token={self._token(client)}")
        cal = ical_lib.Calendar.from_ical(res.content)
        summaries = [
            str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"
        ]
        assert "Export test task" in summaries, \
            f"Expected task in export, got: {summaries}"

    def test_unscheduled_todo_not_in_export(self, client):
        client.post("/api/cards", json={"title": "No date task", "section": "later"})
        res = client.get(f"/api/calendar/export.ics?token={self._token(client)}")
        cal = ical_lib.Calendar.from_ical(res.content)
        summaries = [
            str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"
        ]
        assert "No date task" not in summaries

    def test_completed_todo_not_in_export(self, client):
        todo_id = client.post("/api/cards", json={
            "title": "Completed task",
            "section": "today",
            "scheduled_at": "2026-06-15T10:00:00",
        }).json()["id"]
        client.put(f"/api/cards/{todo_id}", json={"completed": True})

        res = client.get(f"/api/calendar/export.ics?token={self._token(client)}")
        cal = ical_lib.Calendar.from_ical(res.content)
        summaries = [
            str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"
        ]
        assert "Completed task" not in summaries

    def test_exported_dtstart_is_timezone_aware(self, client):
        """Exported DTSTART must carry timezone info so calendar apps parse it correctly."""
        client.post("/api/cards", json={
            "title": "TZ export task",
            "section": "week",
            "scheduled_at": "2026-06-15T14:00:00",
        })
        res = client.get(f"/api/calendar/export.ics?token={self._token(client)}")
        cal = ical_lib.Calendar.from_ical(res.content)
        for component in cal.walk():
            if component.name == "VEVENT" and str(component.get("SUMMARY")) == "TZ export task":
                dtstart = component.get("DTSTART").dt
                assert dtstart.tzinfo is not None, \
                    "Exported DTSTART must be timezone-aware"
                return
        pytest.fail("VEVENT 'TZ export task' not found in exported ICS")

    def test_exported_time_matches_stored_time(self, client):
        """The exported UTC time must represent the same instant as the stored naive time."""
        stored_naive = "2026-06-15T14:00:00"
        client.post("/api/cards", json={
            "title": "Time match task",
            "section": "week",
            "scheduled_at": stored_naive,
        })
        res = client.get(f"/api/calendar/export.ics?token={self._token(client)}")
        cal = ical_lib.Calendar.from_ical(res.content)
        for component in cal.walk():
            if component.name == "VEVENT" and str(component.get("SUMMARY")) == "Time match task":
                dtstart = component.get("DTSTART").dt
                # The export attaches UTC — strip it back to naive for comparison
                exported_naive = dtstart.replace(tzinfo=None)
                assert exported_naive == datetime.fromisoformat(stored_naive), \
                    f"Exported time {exported_naive} != stored {stored_naive}"
                return
        pytest.fail("VEVENT 'Time match task' not found in exported ICS")

    def test_tag_filter_includes_matching_tag(self, client):
        work_id = get_tag_id(client, "work")
        personal_id = get_tag_id(client, "personal")
        client.post("/api/cards", json={
            "title": "Work task",
            "section": "week",
            "scheduled_at": "2026-06-15T10:00:00",
            "tag_ids": [work_id],
        })
        client.post("/api/cards", json={
            "title": "Personal task",
            "section": "week",
            "scheduled_at": "2026-06-15T11:00:00",
            "tag_ids": [personal_id],
        })
        token = self._token(client)
        res = client.get(f"/api/calendar/export.ics?token={token}&tag_id={work_id}")
        cal = ical_lib.Calendar.from_ical(res.content)
        summaries = [str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"]
        assert "Work task" in summaries
        assert "Personal task" not in summaries

    def test_rotated_token_invalidates_old_url(self, client):
        old_token = self._token(client)
        client.post("/api/settings/export-token/rotate")
        res = client.get(f"/api/calendar/export.ics?token={old_token}")
        assert res.status_code == 401


# ── gcal.fetch_events (iCal import / parsing) ────────────────────────────────

_FETCH_START = date.today()
_FETCH_END = date.today() + timedelta(days=7)


def _tomorrow_utc_dtstart() -> str:
    tomorrow = date.today() + timedelta(days=1)
    return tomorrow.strftime("%Y%m%d") + "T140000Z"


def _expected_utc(dtstart_utc: str) -> datetime:
    """Parse a UTC dtstart string (YYYYMMDDTHHMMSSZ) into a UTC-aware datetime."""
    return datetime.strptime(dtstart_utc, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _fetch(ics_content: bytes) -> list[dict]:
    """Call gcal.fetch_events with mocked HTTP response."""
    with patch("gcal.requests.get", return_value=_MockResponse(ics_content)):
        return gcal.fetch_events("http://test/test.ics", _FETCH_START, _FETCH_END)


class TestICalImport:
    """Tests for gcal.fetch_events() with inline ICS data — no network calls."""

    def test_basic_event_parsed(self):
        dtstart = _tomorrow_utc_dtstart()
        events = _fetch(_vcal(_vevent("e1@test", "Team meeting", dtstart)))
        assert len(events) == 1
        assert events[0]["title"] == "Team meeting"

    def test_cancelled_event_excluded(self):
        dtstart = _tomorrow_utc_dtstart()
        events = _fetch(_vcal(_vevent("e2@test", "Cancelled meeting", dtstart, status="CANCELLED")))
        assert events == [], "STATUS:CANCELLED must be excluded"

    def test_cancelled_among_valid_events(self):
        dtstart = _tomorrow_utc_dtstart()
        events = _fetch(_vcal(
            _vevent("good@test", "Valid event", dtstart),
            _vevent("bad@test", "Cancelled event", dtstart, status="CANCELLED"),
        ))
        titles = [e["title"] for e in events]
        assert "Valid event" in titles
        assert "Cancelled event" not in titles

    def test_uid_returned(self):
        dtstart = _tomorrow_utc_dtstart()
        events = _fetch(_vcal(_vevent("my-uid@test", "Tagged event", dtstart)))
        assert events[0]["uid"] == "my-uid@test"

    def test_sequence_returned(self):
        dtstart = _tomorrow_utc_dtstart()
        events = _fetch(_vcal(_vevent("seq@test", "Seq event", dtstart, sequence=3)))
        assert events[0]["sequence"] == 3

    def test_default_sequence_is_zero(self):
        dtstart = _tomorrow_utc_dtstart()
        events = _fetch(_vcal(_vevent("noseq@test", "No seq", dtstart)))
        assert events[0]["sequence"] == 0

    def test_utc_datetime_normalized_to_utc_aware(self):
        """UTC datetimes must be returned as UTC-aware so the frontend converts to local time."""
        dtstart = _tomorrow_utc_dtstart()
        expected = _expected_utc(dtstart)
        events = _fetch(_vcal(_vevent("tz@test", "UTC event", dtstart)))
        assert len(events) == 1
        result_start = events[0]["start"]
        assert result_start == expected, \
            f"Expected UTC-aware {expected}, got {result_start}"
        assert result_start.tzinfo is not None, "Returned datetime must be UTC-aware"
        assert result_start.utcoffset().total_seconds() == 0, "Offset must be zero (UTC)"

    def test_utc_datetime_wall_clock_preserved(self):
        """The wall-clock UTC hour/minute must be unchanged after normalization."""
        dtstart = _tomorrow_utc_dtstart()   # e.g. "20260604T140000Z"
        events = _fetch(_vcal(_vevent("wallclock@test", "Wall clock", dtstart)))
        result_start = events[0]["start"]
        # Parse the expected UTC wall-clock time
        expected_utc = _expected_utc(dtstart)
        assert result_start.hour == expected_utc.hour
        assert result_start.minute == expected_utc.minute

    def test_non_utc_tz_aware_normalized_to_utc(self):
        """Events in non-UTC timezones must be normalized to UTC, not kept as-is."""
        # Build an ICS event in Etc/GMT+5 (a fixed UTC-5 offset, POSIX convention).
        # Use tomorrow so the event falls within the _FETCH_START/_FETCH_END window.
        tomorrow = date.today() + timedelta(days=1)
        dtstart_local = tomorrow.strftime("%Y%m%d") + "T140000"  # 2pm local = 19:00 UTC
        ics = (
            b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
            b"BEGIN:VTIMEZONE\r\nTZID:Etc/GMT+5\r\n"
            b"BEGIN:STANDARD\r\nTZOFFSETFROM:-0500\r\nTZOFFSETTO:-0500\r\n"
            b"TZNAME:EST\r\nDTSTART:19700101T000000\r\nEND:STANDARD\r\n"
            b"END:VTIMEZONE\r\n"
            b"BEGIN:VEVENT\r\n"
            b"UID:tz-eastern@test\r\n"
            b"SUMMARY:Eastern event\r\n"
            + f"DTSTART;TZID=Etc/GMT+5:{dtstart_local}\r\n".encode()
            + b"DTSTAMP:20260101T000000Z\r\n"
            b"END:VEVENT\r\n"
            b"END:VCALENDAR\r\n"
        )
        events = _fetch(ics)
        assert len(events) == 1
        result_start = events[0]["start"]
        assert result_start.tzinfo is not None, "Must be tz-aware"
        assert result_start.utcoffset().total_seconds() == 0, "Must be normalized to UTC"
        # 2pm EST (UTC-5) = 19:00 UTC
        assert result_start.hour == 19, \
            f"Expected 19:00 UTC, got {result_start.hour}:00"

    def test_utc_event_is_not_all_day(self):
        events = _fetch(_vcal(_vevent("timed@test", "Timed", _tomorrow_utc_dtstart())))
        assert events[0]["all_day"] is False

    def test_all_day_event_parsed(self):
        tomorrow = date.today() + timedelta(days=1)
        events = _fetch(_vcal(_vevent_allday("allday@test", "All day event", tomorrow)))
        assert len(events) == 1
        assert events[0]["all_day"] is True

    def test_all_day_event_start_is_midnight(self):
        tomorrow = date.today() + timedelta(days=1)
        events = _fetch(_vcal(_vevent_allday("allday2@test", "All day 2", tomorrow)))
        start = events[0]["start"]
        assert isinstance(start, datetime)
        assert start.hour == 0 and start.minute == 0 and start.second == 0

    def test_multiple_events_all_returned(self):
        dtstart = _tomorrow_utc_dtstart()
        events = _fetch(_vcal(
            _vevent("e1@test", "Event One", dtstart),
            _vevent("e2@test", "Event Two", dtstart),
        ))
        titles = {e["title"] for e in events}
        assert "Event One" in titles
        assert "Event Two" in titles

    def test_description_returned(self):
        dtstart = _tomorrow_utc_dtstart()
        events = _fetch(_vcal(_vevent_with_desc("desc@test", "Described", dtstart, "Meeting notes")))
        assert events[0]["description"] == "Meeting notes"

    def test_event_id_falls_back_to_start_isoformat_when_no_uid(self):
        """Events without UID use start datetime as id."""
        tomorrow = date.today() + timedelta(days=1)
        # Build a vevent with no UID line
        lines = [
            "BEGIN:VEVENT",
            f"SUMMARY:No UID event",
            f"DTSTART:{tomorrow.strftime('%Y%m%d')}T100000Z",
            "DTSTAMP:20260101T000000Z",
            "END:VEVENT",
        ]
        ev_str = "\r\n".join(lines)
        events = _fetch(_vcal(ev_str))
        assert len(events) == 1
        assert events[0]["uid"] == ""


# ── UID deduplication (endpoint level) ───────────────────────────────────────

class TestUIDDeduplication:
    # Fixed start so cross-feed duplicates share the same uid+start dedup key.
    # End is start+1 day so the event is never filtered as already-ended.
    _FIXED_START = datetime.combine(date.today(), time(12, 0), tzinfo=timezone.utc)
    _FIXED_END   = datetime.combine(date.today(), time(12, 0), tzinfo=timezone.utc) + timedelta(days=1)

    def _mock_event(self, uid: str, title: str, sequence: int) -> dict:
        return {
            "id": uid,
            "uid": uid,
            "sequence": sequence,
            "title": title,
            "description": None,
            "start": self._FIXED_START,
            "end": self._FIXED_END,
            "all_day": False,
        }

    def test_higher_sequence_wins(self, client):
        """Same UID across two feeds: only the higher SEQUENCE version appears."""
        work_id = get_tag_id(client, "work")
        personal_id = get_tag_id(client, "personal")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://a.example.com/a.ics"},
            {"tag_id": personal_id, "ical_url": "https://b.example.com/b.ics"},
        ])
        old_ev = self._mock_event("uid-dupe@test", "Old title", sequence=0)
        new_ev = self._mock_event("uid-dupe@test", "Updated title", sequence=2)
        with patch("gcal.fetch_events", side_effect=[[old_ev], [new_ev]]):
            res = client.get("/api/calendar-events")
        events = res.json()
        assert len(events) == 1, f"Expected 1 deduplicated event, got {len(events)}"
        assert events[0]["title"] == "Updated title", \
            f"Higher SEQUENCE should win, got: {events[0]['title']!r}"

    def test_lower_sequence_does_not_overwrite(self, client):
        """If the newer version arrives first, the stale one must not overwrite it."""
        work_id = get_tag_id(client, "work")
        personal_id = get_tag_id(client, "personal")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://a.example.com/a.ics"},
            {"tag_id": personal_id, "ical_url": "https://b.example.com/b.ics"},
        ])
        new_ev = self._mock_event("uid-dupe2@test", "Current version", sequence=5)
        old_ev = self._mock_event("uid-dupe2@test", "Stale version", sequence=1)
        with patch("gcal.fetch_events", side_effect=[[new_ev], [old_ev]]):
            res = client.get("/api/calendar-events")
        events = res.json()
        assert len(events) == 1
        assert events[0]["title"] == "Current version"

    def test_no_uid_events_are_never_deduped(self, client):
        """Events without a UID must each appear even if they share title/time."""
        work_id = get_tag_id(client, "work")
        client.put("/api/calendar-mappings", json=[
            {"tag_id": work_id, "ical_url": "https://example.com/work.ics"}
        ])
        base = {
            "uid": "",
            "sequence": 0,
            "description": None,
            "start": datetime.now(timezone.utc) + timedelta(hours=1),
            "end": None,
            "all_day": False,
        }
        ev1 = {**base, "id": "no-uid-1", "title": "No-UID event A"}
        ev2 = {**base, "id": "no-uid-2", "title": "No-UID event B"}
        with patch("gcal.fetch_events", return_value=[ev1, ev2]):
            res = client.get("/api/calendar-events")
        assert len(res.json()) == 2, \
            f"No-UID events must not be deduplicated, got {len(res.json())}"
