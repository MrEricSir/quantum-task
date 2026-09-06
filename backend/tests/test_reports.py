"""
Tests for reports/generate.py (resolve_period, generate_tag_report,
render_markdown) and GET /api/reports/tag. No LLM involved anywhere in this
file -- the generator is pure DB + Python, unlike the action-item extraction
or briefing features.
"""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from main import app
from deps import get_db
from reports.generate import count_by_period, generate_tag_report, render_markdown, resolve_period

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


class TestResolvePeriod:
    TODAY = date(2026, 8, 19)  # a Wednesday

    def test_today(self):
        assert resolve_period("today", self.TODAY) == (self.TODAY, self.TODAY)

    def test_this_week_starts_monday(self):
        start, end = resolve_period("this_week", self.TODAY)
        assert start == date(2026, 8, 17)  # Monday
        assert end == self.TODAY

    def test_last_week_is_prior_monday_to_sunday(self):
        start, end = resolve_period("last_week", self.TODAY)
        assert start == date(2026, 8, 10)
        assert end == date(2026, 8, 16)

    def test_this_month(self):
        start, end = resolve_period("this_month", self.TODAY)
        assert start == date(2026, 8, 1)
        assert end == self.TODAY

    def test_last_month(self):
        start, end = resolve_period("last_month", self.TODAY)
        assert start == date(2026, 7, 1)
        assert end == date(2026, 7, 31)

    def test_last_month_across_year_boundary(self):
        start, end = resolve_period("last_month", date(2026, 1, 15))
        assert start == date(2025, 12, 1)
        assert end == date(2025, 12, 31)

    def test_last_7_days_includes_today(self):
        start, end = resolve_period("last_7_days", self.TODAY)
        assert (end - start).days == 6
        assert end == self.TODAY

    def test_last_30_days_includes_today(self):
        start, end = resolve_period("last_30_days", self.TODAY)
        assert (end - start).days == 29
        assert end == self.TODAY

    def test_unknown_period_raises(self):
        with pytest.raises(ValueError):
            resolve_period("next_decade", self.TODAY)


class TestGenerateTagReport:
    def test_unknown_tag_returns_none(self):
        with TestingSessionLocal() as db:
            result = generate_tag_report(db, 999, "done", date(2026, 8, 1), date(2026, 8, 31))
        assert result is None

    def test_unknown_mode_raises(self):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            with pytest.raises(ValueError):
                generate_tag_report(db, tag.id, "sideways", date(2026, 8, 1), date(2026, 8, 31))

    def test_done_mode_filters_by_tag_and_completed_date(self):
        with TestingSessionLocal() as db:
            work = models.Tag(name="work")
            other = models.Tag(name="other")
            db.add_all([work, other])
            db.commit()
            db.add(models.Card(
                title="In range, tagged", section="today", completed=True,
                completed_at=datetime(2026, 8, 15, 12, 0), tags=[work],
            ))
            db.add(models.Card(
                title="Out of range, tagged", section="today", completed=True,
                completed_at=datetime(2026, 7, 1, 12, 0), tags=[work],
            ))
            db.add(models.Card(
                title="In range, wrong tag", section="today", completed=True,
                completed_at=datetime(2026, 8, 15, 12, 0), tags=[other],
            ))
            db.add(models.Card(
                title="In range, tagged, not completed", section="today",
                completed=False, tags=[work],
            ))
            db.commit()

            report = generate_tag_report(db, work.id, "done", date(2026, 8, 1), date(2026, 8, 31))
        assert report["count"] == 1
        assert report["items"][0]["title"] == "In range, tagged"
        assert report["tag_name"] == "work"

    def test_todo_mode_includes_dated_items_in_range_and_undated_backlog(self):
        with TestingSessionLocal() as db:
            work = models.Tag(name="work")
            db.add(work)
            db.commit()
            db.add(models.Card(
                title="Scheduled in range", section="week",
                scheduled_at=datetime(2026, 8, 20, 9, 0), tags=[work],
            ))
            db.add(models.Card(
                title="Scheduled out of range", section="week",
                scheduled_at=datetime(2026, 9, 20, 9, 0), tags=[work],
            ))
            db.add(models.Card(title="No date, backlog", section="later", tags=[work]))
            db.add(models.Card(
                title="Completed, should be excluded", section="today",
                completed=True, completed_at=datetime(2026, 8, 20), tags=[work],
            ))
            db.commit()

            report = generate_tag_report(db, work.id, "todo", date(2026, 8, 1), date(2026, 8, 31))
        titles = {i["title"] for i in report["items"]}
        assert titles == {"Scheduled in range", "No date, backlog"}

    def test_archived_cards_excluded_from_todo(self):
        with TestingSessionLocal() as db:
            work = models.Tag(name="work")
            db.add(work)
            db.commit()
            db.add(models.Card(title="Archived", section="later", archived=True, tags=[work]))
            db.commit()
            report = generate_tag_report(db, work.id, "todo", date(2026, 8, 1), date(2026, 8, 31))
        assert report["count"] == 0

    def test_timezone_boundary_respects_local_date(self):
        # 2026-08-15 23:30 UTC is 2026-08-16 local at tz_offset=-60 (UTC+1).
        with TestingSessionLocal() as db:
            work = models.Tag(name="work")
            db.add(work)
            db.commit()
            db.add(models.Card(
                title="Late UTC, next day local", section="today", completed=True,
                completed_at=datetime(2026, 8, 15, 23, 30), tags=[work],
            ))
            db.commit()
            # Window is Aug 16 only -- UTC date (15th) would miss it, local date (16th) should not.
            report = generate_tag_report(
                db, work.id, "done", date(2026, 8, 16), date(2026, 8, 16), tz_offset=-60,
            )
        assert report["count"] == 1

    def test_todo_mode_scheduled_at_is_not_offset_shifted(self):
        """Regression test: scheduled_at is already a naive LOCAL wall-clock value (see
        models.py's docstring), unlike completed_at which is a UTC instant -- applying
        the tz_offset conversion to it (as an earlier version of this code did, via a
        _to_local_date() call shared with the "done" branch) shifts it onto the wrong
        calendar day near midnight. A card scheduled for local Aug 16 must stay on Aug
        16 regardless of tz_offset, since the value was never in UTC to begin with."""
        with TestingSessionLocal() as db:
            work = models.Tag(name="work")
            db.add(work)
            db.commit()
            db.add(models.Card(
                title="Scheduled near local midnight", section="week",
                scheduled_at=datetime(2026, 8, 16, 23, 30), tags=[work],
            ))
            db.commit()

            report = generate_tag_report(
                db, work.id, "todo", date(2026, 8, 16), date(2026, 8, 16), tz_offset=-60,
            )
        assert report["count"] == 1
        assert report["items"][0]["date"] == "2026-08-16"


class TestCountByPeriod:
    TODAY = date(2026, 8, 19)  # a Wednesday; this_week starts 2026-08-17

    def test_unknown_tag_returns_none(self):
        with TestingSessionLocal() as db:
            result = count_by_period(db, 999, "done", self.TODAY)
        assert result is None

    def test_unknown_mode_raises(self):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            with pytest.raises(ValueError):
                count_by_period(db, tag.id, "sideways", self.TODAY)

    def test_counts_one_key_per_period_choice(self):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            counts = count_by_period(db, tag.id, "done", self.TODAY)
        from reports.generate import PERIOD_CHOICES
        assert set(counts.keys()) == set(PERIOD_CHOICES)
        assert all(v == 0 for v in counts.values())

    def test_done_item_counts_toward_containing_periods_only(self):
        with TestingSessionLocal() as db:
            work = models.Tag(name="work")
            db.add(work)
            db.commit()
            # This week (Aug 17-19) -- also within this_month, last_7_days, last_30_days,
            # but NOT today or last_week.
            db.add(models.Card(
                title="Done this week", section="today", completed=True,
                completed_at=datetime(2026, 8, 18, 12, 0), tags=[work],
            ))
            db.commit()
            counts = count_by_period(db, work.id, "done", self.TODAY)
        assert counts["this_week"] == 1
        assert counts["this_month"] == 1
        assert counts["last_7_days"] == 1
        assert counts["last_30_days"] == 1
        assert counts["today"] == 0
        assert counts["last_week"] == 0
        assert counts["last_month"] == 0

    def test_undated_todo_item_counts_toward_every_period(self):
        with TestingSessionLocal() as db:
            work = models.Tag(name="work")
            db.add(work)
            db.commit()
            db.add(models.Card(title="Backlog item", section="later", tags=[work]))
            db.commit()
            counts = count_by_period(db, work.id, "todo", self.TODAY)
        from reports.generate import PERIOD_CHOICES
        assert all(counts[p] == 1 for p in PERIOD_CHOICES)

    def test_matches_generate_tag_report_count_for_each_period(self):
        # count_by_period is a separate code path from generate_tag_report (one
        # query pass vs. per-period filtering) -- they must agree on every period.
        with TestingSessionLocal() as db:
            work = models.Tag(name="work")
            db.add(work)
            db.commit()
            work_id = work.id
            db.add(models.Card(
                title="Done today", section="today", completed=True,
                completed_at=datetime(2026, 8, 19, 12, 0), tags=[work],
            ))
            db.add(models.Card(
                title="Done last month", section="today", completed=True,
                completed_at=datetime(2026, 7, 10, 12, 0), tags=[work],
            ))
            db.commit()

        from reports.generate import PERIOD_CHOICES
        with TestingSessionLocal() as db:
            counts = count_by_period(db, work_id, "done", self.TODAY)
        for period in PERIOD_CHOICES:
            with TestingSessionLocal() as db:
                start, end = resolve_period(period, self.TODAY)
                report = generate_tag_report(db, work_id, "done", start, end)
            assert counts[period] == report["count"], f"mismatch for period={period!r}"


class TestRenderMarkdown:
    def test_empty_items_shows_nothing_found(self):
        report = {"tag_name": "work", "mode": "done", "start": "2026-08-01", "end": "2026-08-31", "items": []}
        md = render_markdown(report)
        assert "Nothing found" in md

    def test_items_rendered_as_bullets_with_dates(self):
        report = {
            "tag_name": "work", "mode": "done", "start": "2026-08-01", "end": "2026-08-31",
            "items": [{"id": 1, "title": "Ship the fix", "date": "2026-08-15"}],
        }
        md = render_markdown(report)
        assert "- Ship the fix (2026-08-15)" in md

    def test_undated_item_has_no_trailing_parens(self):
        report = {
            "tag_name": "work", "mode": "todo", "start": "2026-08-01", "end": "2026-08-31",
            "items": [{"id": 1, "title": "Someday task", "date": None}],
        }
        md = render_markdown(report)
        assert "- Someday task\n" in md or md.endswith("- Someday task")


class TestReportEndpoint:
    def test_missing_period_and_range_returns_400(self, client):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            tag_id = tag.id
        r = client.get(f"/api/reports/tag?tag_id={tag_id}&mode=done")
        assert r.status_code == 400

    def test_invalid_mode_returns_400(self, client):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            tag_id = tag.id
        r = client.get(f"/api/reports/tag?tag_id={tag_id}&mode=sideways&period=this_week")
        assert r.status_code == 400

    def test_invalid_period_returns_400(self, client):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            tag_id = tag.id
        r = client.get(f"/api/reports/tag?tag_id={tag_id}&mode=done&period=next_decade")
        assert r.status_code == 400

    def test_unknown_tag_returns_404(self, client):
        r = client.get("/api/reports/tag?tag_id=999&mode=done&period=this_week")
        assert r.status_code == 404

    def test_period_shortcut_returns_report_with_markdown(self, client):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            tag_id = tag.id
        r = client.get(f"/api/reports/tag?tag_id={tag_id}&mode=done&period=this_week")
        assert r.status_code == 200
        body = r.json()
        assert body["tag_name"] == "work"
        assert "markdown" in body

    def test_custom_range_returns_report(self, client):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            tag_id = tag.id
        r = client.get(f"/api/reports/tag?tag_id={tag_id}&mode=todo&start=2026-08-01&end=2026-08-31")
        assert r.status_code == 200


class TestPeriodCountsEndpoint:
    def test_invalid_mode_returns_400(self, client):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            tag_id = tag.id
        r = client.get(f"/api/reports/tag/period-counts?tag_id={tag_id}&mode=sideways")
        assert r.status_code == 400

    def test_unknown_tag_returns_404(self, client):
        r = client.get("/api/reports/tag/period-counts?tag_id=999&mode=done")
        assert r.status_code == 404

    def test_returns_counts_for_every_period(self, client):
        with TestingSessionLocal() as db:
            tag = models.Tag(name="work")
            db.add(tag)
            db.commit()
            tag_id = tag.id
        r = client.get(f"/api/reports/tag/period-counts?tag_id={tag_id}&mode=done")
        assert r.status_code == 200
        from reports.generate import PERIOD_CHOICES
        assert set(r.json()["counts"].keys()) == set(PERIOD_CHOICES)
