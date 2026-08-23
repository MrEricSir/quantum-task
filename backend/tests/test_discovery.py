"""
Tests for GET /api/discovery/events -- primarily Phase 1 of DISCOVERY_IMPROVEMENTS.md
(concurrent iCal fetching for feeds whose cache is stale): every stale feed's
gcal.fetch_events call actually happens, results merge correctly regardless of
completion order, a fetch error on one feed doesn't take down the others, and
fresh-cache feeds are never re-fetched at all.

No dedicated test file existed for this router before -- everything here is new
coverage, not a regression suite for existing behavior.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import time
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
import routers.discovery as discovery

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
def setup_db(monkeypatch):
    discovery._ranking_cache.clear()
    discovery._ranking_inflight.clear()
    models.Base.metadata.create_all(bind=test_engine)
    # _persist_ranking / _refine_interests_bg run as background tasks and open their own
    # session via `from database import SessionLocal` (there's no request-scoped session by
    # then) -- without this, they'd write straight to the real dev todos.db instead of the
    # in-memory test DB.
    monkeypatch.setattr(database, "SessionLocal", TestingSessionLocal)
    yield
    models.Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _make_feed(name, url, last_fetched=None, cached_events=None):
    with TestingSessionLocal() as db:
        feed = models.EventDiscoveryFeed(
            name=name, ical_url=url, last_fetched=last_fetched, cached_events=cached_events,
        )
        db.add(feed)
        db.commit()
        db.refresh(feed)
        return feed.id


def _event(title, days_from_now=1, uid=None):
    start = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    return {
        "id": title.lower().replace(" ", "-"),
        "uid": uid or f"{title.lower().replace(' ', '-')}@example.com",
        "sequence": 0,
        "title": title,
        "description": None,
        "location": None,
        "url": None,
        "start": start,
        "end": start + timedelta(hours=1),
        "all_day": False,
    }


def _timed_event(title, start, end=None, uid=None):
    return {
        "id": title.lower().replace(" ", "-"),
        "uid": uid or f"{title.lower().replace(' ', '-')}@example.com",
        "sequence": 0,
        "title": title,
        "description": None,
        "location": None,
        "url": None,
        "start": start,
        "end": end,
        "all_day": False,
    }


def _all_day_event(title, start_date, end_date=None, uid=None):
    return {
        "id": title.lower().replace(" ", "-"),
        "uid": uid or f"{title.lower().replace(' ', '-')}@example.com",
        "sequence": 0,
        "title": title,
        "description": None,
        "location": None,
        "url": None,
        "start": start_date,
        "end": end_date,
        "all_day": True,
    }


class TestConcurrentFetch:
    def test_fetches_every_stale_feed_and_merges_results(self, client):
        _make_feed("Feed A", "https://a.example/feed.ics")
        _make_feed("Feed B", "https://b.example/feed.ics")
        _make_feed("Feed C", "https://c.example/feed.ics")

        def fake_fetch(url, start, end):
            name = {"https://a.example/feed.ics": "A", "https://b.example/feed.ics": "B",
                    "https://c.example/feed.ics": "C"}[url]
            return [_event(f"Event {name}")]

        with patch("routers.discovery.gcal_lib.fetch_events", side_effect=fake_fetch) as mock_fetch:
            res = client.get("/api/discovery/events")

        assert res.status_code == 200
        assert mock_fetch.call_count == 3
        titles = {e["title"] for e in res.json()}
        assert titles == {"Event A", "Event B", "Event C"}

    def test_results_merge_correctly_regardless_of_completion_order(self, client):
        """The slow feed finishes last but its event must still show up -- concurrent
        fetching must not silently drop or misattribute a feed's results."""
        _make_feed("Fast", "https://fast.example/feed.ics")
        _make_feed("Slow", "https://slow.example/feed.ics")

        def fake_fetch(url, start, end):
            if "slow" in url:
                time.sleep(0.05)
                return [_event("Slow Event")]
            return [_event("Fast Event")]

        with patch("routers.discovery.gcal_lib.fetch_events", side_effect=fake_fetch):
            res = client.get("/api/discovery/events")

        titles = {e["title"] for e in res.json()}
        assert titles == {"Fast Event", "Slow Event"}

    def test_stale_feeds_are_fetched_concurrently_not_serially(self, client):
        """The actual regression this phase exists to prevent: confirm three feeds that
        each take 0.2s finish in ~0.2s total, not ~0.6s -- proves the fetches genuinely
        overlap, not just that the code is structured to look like it does."""
        for i in range(3):
            _make_feed(f"Feed {i}", f"https://{i}.example/feed.ics")

        def fake_fetch(url, start, end):
            time.sleep(0.2)
            return [_event("Event")]

        with patch("routers.discovery.gcal_lib.fetch_events", side_effect=fake_fetch):
            start_time = time.monotonic()
            res = client.get("/api/discovery/events")
            elapsed = time.monotonic() - start_time

        assert res.status_code == 200
        assert elapsed < 0.5, f"took {elapsed:.2f}s -- fetches don't appear to be concurrent"

    def test_fresh_cached_feed_is_never_refetched(self, client):
        recent = datetime.utcnow() - timedelta(minutes=5)
        cached = discovery._serialize_gcal_events([_event("Cached Event")])
        _make_feed("Fresh", "https://fresh.example/feed.ics", last_fetched=recent, cached_events=cached)

        with patch("routers.discovery.gcal_lib.fetch_events") as mock_fetch:
            res = client.get("/api/discovery/events")

        mock_fetch.assert_not_called()
        titles = {e["title"] for e in res.json()}
        assert titles == {"Cached Event"}

    def test_one_feeds_fetch_error_does_not_affect_the_others(self, client):
        _make_feed("Broken", "https://broken.example/feed.ics")
        _make_feed("Working", "https://working.example/feed.ics")

        def fake_fetch(url, start, end):
            if "broken" in url:
                raise ConnectionError("boom")
            return [_event("Working Event")]

        with patch("routers.discovery.gcal_lib.fetch_events", side_effect=fake_fetch):
            res = client.get("/api/discovery/events")

        assert res.status_code == 200
        titles = {e["title"] for e in res.json()}
        assert titles == {"Working Event"}

    def test_fetch_error_falls_back_to_stale_cache_when_available(self, client):
        stale = datetime.utcnow() - timedelta(hours=10)  # older than the 3h TTL
        cached = discovery._serialize_gcal_events([_event("Stale Cached Event")])
        _make_feed("Flaky", "https://flaky.example/feed.ics", last_fetched=stale, cached_events=cached)

        with patch("routers.discovery.gcal_lib.fetch_events", side_effect=ConnectionError("boom")):
            res = client.get("/api/discovery/events")

        assert res.status_code == 200
        titles = {e["title"] for e in res.json()}
        assert titles == {"Stale Cached Event"}

    def test_successful_fetches_are_persisted_in_one_batch_commit(self, client):
        """DB writes happen back on the main thread after every worker-thread fetch
        finishes, not one commit per feed -- confirm the persisted state is correct
        regardless of how that's batched."""
        feed_id = _make_feed("Feed A", "https://a.example/feed.ics")

        with patch("routers.discovery.gcal_lib.fetch_events", return_value=[_event("Event A")]):
            client.get("/api/discovery/events")

        with TestingSessionLocal() as db:
            feed = db.query(models.EventDiscoveryFeed).filter_by(id=feed_id).first()
            assert feed.last_fetched is not None
            assert feed.cached_events is not None
            assert "Event A" in feed.cached_events


class TestPastEventCutoff:
    """Phase 2 of DISCOVERY_IMPROVEMENTS.md: excludes timed events that are about to end
    (not just ones that already have), without penalizing an event just because it started
    a while ago -- and fixes a related bug where a multi-day all-day event disappeared the
    moment its START date passed, regardless of whether it was still ongoing."""

    def _get(self, client, events, local_date=None):
        _make_feed("Feed", "https://feed.example/feed.ics")
        headers = {"X-Local-Date": local_date} if local_date else {}
        with patch("routers.discovery.gcal_lib.fetch_events", return_value=events):
            res = client.get("/api/discovery/events", headers=headers)
        assert res.status_code == 200
        return {e["title"] for e in res.json()}

    def test_timed_event_ending_within_the_hour_is_excluded(self, client):
        now = datetime.now(timezone.utc)
        ev = _timed_event("Almost Over", start=now - timedelta(hours=2), end=now + timedelta(minutes=30))
        assert self._get(client, [ev]) == set()

    def test_timed_event_ending_beyond_the_hour_is_kept(self, client):
        now = datetime.now(timezone.utc)
        ev = _timed_event("Plenty Of Time", start=now - timedelta(hours=1), end=now + timedelta(minutes=90))
        assert self._get(client, [ev]) == {"Plenty Of Time"}

    def test_fully_ended_timed_event_is_excluded(self, client):
        now = datetime.now(timezone.utc)
        ev = _timed_event("Already Over", start=now - timedelta(hours=3), end=now - timedelta(hours=1))
        assert self._get(client, [ev]) == set()

    def test_multi_day_timed_event_that_started_a_while_ago_is_not_penalized(self, client):
        """A long event that merely STARTED a while back must not be treated as "nearly
        over" just because of its start time -- only how much time is left before it ends
        should matter."""
        now = datetime.now(timezone.utc)
        ev = _timed_event("Multi-Day Conference", start=now - timedelta(days=2), end=now + timedelta(days=1))
        assert self._get(client, [ev]) == {"Multi-Day Conference"}

    def test_no_end_time_event_not_yet_started_is_kept(self, client):
        """Regression guard: a start-only event (no DTEND) starting soon must not be
        excluded by the "nearly over" buffer -- that buffer only makes sense relative to an
        actual end time, and applying it to a start-only event would hide something that
        hasn't even happened yet."""
        now = datetime.now(timezone.utc)
        ev = _timed_event("Starting Soon", start=now + timedelta(minutes=30), end=None)
        assert self._get(client, [ev]) == {"Starting Soon"}

    def test_no_end_time_event_already_started_is_excluded(self, client):
        now = datetime.now(timezone.utc)
        ev = _timed_event("Already Started", start=now - timedelta(minutes=5), end=None)
        assert self._get(client, [ev]) == set()

    # Fixed anchor date, passed explicitly via X-Local-Date -- the endpoint's "today" comes
    # from that header (falling back to the server's own local date when absent), so pinning
    # it here keeps these date-boundary tests deterministic regardless of what time or
    # timezone the test happens to run in.
    _TODAY = date(2026, 6, 15)

    def test_multi_day_all_day_event_still_ongoing_is_kept(self, client):
        """The bug this phase fixes: a 3-day festival that started yesterday must still show
        up today and tomorrow, not vanish the instant its start date passes."""
        today = self._TODAY
        ev = _all_day_event("Festival", start_date=today - timedelta(days=1), end_date=today + timedelta(days=2))
        assert self._get(client, [ev], local_date=today.isoformat()) == {"Festival"}

    def test_all_day_event_that_fully_ended_is_excluded(self, client):
        today = self._TODAY
        ev = _all_day_event("Old Fair", start_date=today - timedelta(days=5), end_date=today - timedelta(days=3))
        assert self._get(client, [ev], local_date=today.isoformat()) == set()

    def test_single_day_all_day_event_today_with_no_explicit_end_is_kept(self, client):
        """A feed that omits DTEND for a single-day all-day event implies a 1-day event
        (DTEND would be exclusive, i.e. tomorrow) -- must not be treated as already over."""
        today = self._TODAY
        ev = _all_day_event("Today Only", start_date=today, end_date=None)
        assert self._get(client, [ev], local_date=today.isoformat()) == {"Today Only"}

    def test_single_day_all_day_event_yesterday_with_no_explicit_end_is_excluded(self, client):
        today = self._TODAY
        ev = _all_day_event("Yesterday Only", start_date=today - timedelta(days=1), end_date=None)
        assert self._get(client, [ev], local_date=today.isoformat()) == set()


class TestFetchTimeout:
    def test_fetch_events_uses_a_tightened_timeout(self):
        """See DISCOVERY_IMPROVEMENTS.md's Phase 1 -- was (10, 45), tightened so one
        pathological feed can't hold up the whole discovery request for the better
        part of a minute."""
        import gcal

        captured = {}
        class FakeResponse:
            status_code = 200
            url = "https://example.com/feed.ics"
            headers = {"Content-Type": "text/calendar"}
            content = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
            def raise_for_status(self):
                pass

        def fake_get(url, timeout=None, headers=None):
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("gcal.requests.get", side_effect=fake_get):
            gcal.fetch_events("https://example.com/feed.ics",
                              datetime.now(timezone.utc).date(),
                              (datetime.now(timezone.utc) + timedelta(days=1)).date())

        assert captured["timeout"] == (5, 15)


def _dated_events(*offsets, today):
    """Minimal event dicts for _select_ranking_batch -- only "id" and "start" matter."""
    return [
        {
            "id": f"ev-{offset}-{i}",
            "start": datetime.combine(today + timedelta(days=offset), datetime.min.time(), tzinfo=timezone.utc),
        }
        for i, offset in enumerate(offsets)
    ]


class TestSelectRankingBatch:
    """Unit tests for discovery._select_ranking_batch -- Phase 3's fix for "same-day only"
    discovery. The old code sent events[:_RANK_BATCH_SIZE] (the chronologically-first N) to
    the LLM for scoring, so a busy today/tomorrow could fill the whole batch before an event
    further out was ever even considered. This buckets the batch across the event window
    instead."""

    def test_busy_near_term_day_no_longer_crowds_out_the_whole_batch(self):
        today = date(2026, 6, 15)
        near = _dated_events(*([0] * 20), today=today)
        far = _dated_events(20, today=today)
        events = sorted(near + far, key=lambda e: e["start"])

        batch = discovery._select_ranking_batch(events, today, discovery._RANK_BATCH_SIZE)

        assert len(batch) == discovery._RANK_BATCH_SIZE
        assert any((e["start"].date() - today).days == 20 for e in batch)

    def test_each_bucket_is_represented_when_events_exist_in_every_bucket(self):
        today = date(2026, 6, 15)
        offsets = (0, 1, 4, 5, 8, 10, 15, 25)
        events = _dated_events(*offsets, today=today)

        batch = discovery._select_ranking_batch(events, today, discovery._RANK_BATCH_SIZE)

        selected_offsets = {(e["start"].date() - today).days for e in batch}
        assert selected_offsets == set(offsets)

    def test_leftover_quota_from_empty_near_term_buckets_rolls_forward(self):
        today = date(2026, 6, 15)
        # Nothing in the first two buckets (days 0-6) -- every event is 7+ days out.
        events = _dated_events(*range(7, 28), today=today)

        batch = discovery._select_ranking_batch(events, today, discovery._RANK_BATCH_SIZE)

        assert len(batch) == discovery._RANK_BATCH_SIZE

    def test_batch_size_is_never_exceeded_even_with_many_events_in_every_bucket(self):
        today = date(2026, 6, 15)
        events = _dated_events(*[i % 28 for i in range(200)], today=today)

        batch = discovery._select_ranking_batch(events, today, discovery._RANK_BATCH_SIZE)

        assert len(batch) == discovery._RANK_BATCH_SIZE

    def test_batch_is_returned_in_chronological_order(self):
        today = date(2026, 6, 15)
        events = _dated_events(25, 1, 10, 0, 15, 5, today=today)

        batch = discovery._select_ranking_batch(events, today, discovery._RANK_BATCH_SIZE)

        starts = [e["start"] for e in batch]
        assert starts == sorted(starts)

    def test_fewer_events_than_batch_size_returns_all_of_them(self):
        today = date(2026, 6, 15)
        events = _dated_events(0, 5, 10, 20, today=today)

        batch = discovery._select_ranking_batch(events, today, discovery._RANK_BATCH_SIZE)

        assert len(batch) == 4


class TestRankingBatchEndToEnd:
    """Confirms the fix all the way through the ranking pipeline, not just the selection
    helper in isolation: with interests configured and a busy near-term day, an event three
    weeks out actually reaches the LLM and shows up in the final ranked result."""

    def test_far_out_event_gets_scored_when_near_term_is_busy(self, client):
        """Realistic scoring, not a tie: the far event is the best match, the 20 near-term
        ones are mediocre. Before Phase 3, the far event never even reached the LLM (it
        wasn't in the chronological-first-12 slice), so it had no way to outrank them no
        matter how good a match it was."""
        _make_feed("Feed", "https://feed.example/feed.ics")
        client.put("/api/discovery/interests", json={"interests": "music and art"})

        base = datetime.now(timezone.utc) + timedelta(hours=2)
        near_events = [
            _timed_event(f"Near {i}", base + timedelta(minutes=i), base + timedelta(hours=1, minutes=i))
            for i in range(20)
        ]
        far_event = _timed_event("Far Concert", base + timedelta(days=20), base + timedelta(days=20, hours=1))
        events = near_events + [far_event]

        def fake_create(**kwargs):
            sent = json.loads(kwargs["messages"][1]["content"].split("Events:\n", 1)[1])
            results = [
                {"id": ev["id"], "score": 10 if ev["title"] == "Far Concert" else 3, "reason": "matches"}
                for ev in sent
            ]
            resp = MagicMock()
            resp.choices = [MagicMock(message=MagicMock(content=json.dumps({"results": results})))]
            return resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("routers.discovery.gcal_lib.fetch_events", return_value=events), \
             patch("routers.discovery.llm_client", return_value=mock_client):
            res1 = client.get("/api/discovery/events")
            assert res1.status_code == 200
            # Background ranking task runs synchronously within TestClient's request
            # dispatch, so by the second call the cache is populated with the real result.
            res2 = client.get("/api/discovery/events")
            assert res2.status_code == 200

        titles = {e["title"] for e in res2.json()}
        assert "Far Concert" in titles


def _dup_event(title, start, url=None, description=None):
    """Minimal event dict for _merge_cross_feed_duplicates -- only the fields that function
    actually reads."""
    return {"title": title, "start": start, "url": url, "description": description}


class TestMergeCrossFeedDuplicates:
    """Unit tests for discovery._merge_cross_feed_duplicates -- Phase 4's fix for the same
    real-world event appearing twice because two different feeds (e.g. a city calendar and a
    local events aggregator) each mint their own uid for it, so the existing uid-based dedup
    never sees them as related."""

    def test_same_title_close_start_times_are_merged(self):
        base = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
        events = [
            _dup_event("Farmers Market", base),
            _dup_event("Farmers Market", base + timedelta(minutes=30)),
        ]
        merged = discovery._merge_cross_feed_duplicates(events)
        assert len(merged) == 1

    def test_title_normalization_ignores_case_and_punctuation(self):
        base = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
        events = [
            _dup_event("Farmers Market!!", base),
            _dup_event("  farmers   market", base + timedelta(minutes=10)),
        ]
        merged = discovery._merge_cross_feed_duplicates(events)
        assert len(merged) == 1

    def test_same_title_far_apart_in_time_is_not_merged(self):
        base = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        events = [
            _dup_event("Farmers Market", base),
            _dup_event("Farmers Market", base + timedelta(hours=5)),
        ]
        merged = discovery._merge_cross_feed_duplicates(events)
        assert len(merged) == 2

    def test_different_titles_at_the_same_time_are_not_merged(self):
        base = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
        events = [
            _dup_event("Farmers Market", base),
            _dup_event("Jazz Concert", base),
        ]
        merged = discovery._merge_cross_feed_duplicates(events)
        assert len(merged) == 2

    def test_richer_copy_with_a_url_is_kept_over_one_without(self):
        base = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
        events = [
            _dup_event("Farmers Market", base, url=None),
            _dup_event("Farmers Market", base + timedelta(minutes=15), url="https://example.com/market"),
        ]
        merged = discovery._merge_cross_feed_duplicates(events)
        assert len(merged) == 1
        assert merged[0]["url"] == "https://example.com/market"

    def test_longer_description_is_kept_as_a_tiebreak_when_neither_has_a_url(self):
        base = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
        events = [
            _dup_event("Farmers Market", base, description="Short."),
            _dup_event("Farmers Market", base + timedelta(minutes=15),
                       description="A much longer and more detailed description."),
        ]
        merged = discovery._merge_cross_feed_duplicates(events)
        assert len(merged) == 1
        assert merged[0]["description"] == "A much longer and more detailed description."

    def test_unrelated_events_around_a_duplicate_pair_are_all_preserved(self):
        base = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
        events = [
            _dup_event("Morning Yoga", base - timedelta(hours=6)),
            _dup_event("Farmers Market", base),
            _dup_event("Farmers Market", base + timedelta(minutes=20)),
            _dup_event("Evening Trivia", base + timedelta(hours=4)),
        ]
        merged = discovery._merge_cross_feed_duplicates(events)
        titles = [e["title"] for e in merged]
        assert titles == ["Morning Yoga", "Farmers Market", "Evening Trivia"]


class TestDuplicateDetectionEndToEnd:
    """Confirms cross-feed duplicate merging through the full GET /api/discovery/events
    pipeline: two different feeds each list the same real-world event under a different
    uid, and only one card comes back."""

    def test_same_event_from_two_feeds_is_shown_once(self, client):
        _make_feed("City Calendar", "https://city.example/feed.ics")
        _make_feed("Things To Do", "https://things.example/feed.ics")

        base = datetime.now(timezone.utc) + timedelta(days=2)
        city_event = _timed_event("Farmers Market", base, base + timedelta(hours=2), uid="city-1@example.com")
        aggregator_event = _timed_event(
            "Farmers Market", base + timedelta(minutes=20), base + timedelta(hours=2),
            uid="agg-1@example.com",
        )

        def fake_fetch(url, start, end):
            return [city_event] if "city" in url else [aggregator_event]

        with patch("routers.discovery.gcal_lib.fetch_events", side_effect=fake_fetch):
            res = client.get("/api/discovery/events")

        assert res.status_code == 200
        titles = [e["title"] for e in res.json()]
        assert titles.count("Farmers Market") == 1


def _persistable_event(id_, title, start, uid=None):
    return {
        "id": id_, "uid": uid or f"{id_}@example.com", "title": title,
        "description": None, "location": None, "url": None,
        "start": start, "end": None, "all_day": False,
        "feed_name": None, "score": None, "reason": None,
    }


class TestRankingPersistence:
    """Unit tests for Phase 5: LLM ranking results are persisted to the DB so a Cloud Run
    cold start (which wipes the in-memory _ranking_cache but not the DB) can reuse a
    previous instance's ranking instead of re-running the LLM. See
    DISCOVERY_IMPROVEMENTS.md."""

    def test_persist_then_load_round_trips(self):
        events = [_persistable_event("ev-1", "Concert", datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc))]

        discovery._persist_ranking("test-rkey", events)

        with TestingSessionLocal() as db:
            loaded = discovery._load_persisted_ranking(db, "test-rkey")
        assert loaded == events

    def test_load_returns_none_when_no_row_exists(self):
        with TestingSessionLocal() as db:
            assert discovery._load_persisted_ranking(db, "nonexistent-rkey") is None

    def test_persisting_the_same_rkey_twice_updates_rather_than_duplicates(self):
        events_v1 = [_persistable_event("a", "A", datetime(2026, 6, 15, tzinfo=timezone.utc))]
        events_v2 = [_persistable_event("b", "B", datetime(2026, 6, 16, tzinfo=timezone.utc))]

        discovery._persist_ranking("same-rkey", events_v1)
        discovery._persist_ranking("same-rkey", events_v2)

        with TestingSessionLocal() as db:
            assert db.query(models.DiscoveryRankingCache).filter_by(rkey="same-rkey").count() == 1
            loaded = discovery._load_persisted_ranking(db, "same-rkey")
        assert loaded[0]["title"] == "B"

    def test_row_count_is_capped_at_ranking_cache_max(self):
        for i in range(discovery._RANKING_CACHE_MAX + 5):
            discovery._persist_ranking(
                f"rkey-{i}",
                [_persistable_event("x", "X", datetime(2026, 6, 15, tzinfo=timezone.utc))],
            )

        with TestingSessionLocal() as db:
            count = db.query(models.DiscoveryRankingCache).count()
        assert count == discovery._RANKING_CACHE_MAX


class TestRankingPersistenceEndToEnd:
    def test_cold_start_reuses_a_previously_persisted_ranking_without_recalling_the_llm(self, client):
        _make_feed("Feed", "https://feed.example/feed.ics")
        client.put("/api/discovery/interests", json={"interests": "music"})

        base = datetime.now(timezone.utc) + timedelta(hours=2)
        events = [_timed_event("Concert", base, base + timedelta(hours=2))]

        call_count = {"n": 0}

        def fake_create(**kwargs):
            call_count["n"] += 1
            sent = json.loads(kwargs["messages"][1]["content"].split("Events:\n", 1)[1])
            results = [{"id": ev["id"], "score": 8, "reason": "matches"} for ev in sent]
            resp = MagicMock()
            resp.choices = [MagicMock(message=MagicMock(content=json.dumps({"results": results})))]
            return resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("routers.discovery.gcal_lib.fetch_events", return_value=events), \
             patch("routers.discovery.llm_client", return_value=mock_client):
            res1 = client.get("/api/discovery/events")
            assert res1.status_code == 200
            res2 = client.get("/api/discovery/events")
            assert res2.status_code == 200
            assert res2.headers["X-Ranking-Status"] == "ready"
            assert call_count["n"] == 1

            # Simulate a cold start: the in-memory cache is gone, but the DB row survives.
            discovery._ranking_cache.clear()
            discovery._ranking_inflight.clear()

            res3 = client.get("/api/discovery/events")
            assert res3.status_code == 200
            assert res3.headers["X-Ranking-Status"] == "ready"
            # The LLM must not have been called again -- the persisted ranking was reused.
            assert call_count["n"] == 1


class TestDisplayResultCap:
    """Phase 6: the result cap was raised from 10 to _DISPLAY_RESULT_CAP (20) -- with Phase
    3's batch selection now giving farther-out events a real chance to be scored, a 10-cap
    was throwing away more of the newly fair candidate pool than it needed to."""

    def test_no_interests_chronological_fallback_returns_up_to_the_cap(self, client):
        _make_feed("Feed", "https://feed.example/feed.ics")
        base = datetime.now(timezone.utc) + timedelta(hours=2)
        events = [_timed_event(f"Event {i}", base + timedelta(hours=i)) for i in range(25)]

        with patch("routers.discovery.gcal_lib.fetch_events", return_value=events):
            res = client.get("/api/discovery/events")

        assert res.status_code == 200
        assert len(res.json()) == discovery._DISPLAY_RESULT_CAP

    def test_ranked_results_are_padded_up_to_the_cap_not_just_the_old_ten(self, client):
        _make_feed("Feed", "https://feed.example/feed.ics")
        client.put("/api/discovery/interests", json={"interests": "anything"})

        base = datetime.now(timezone.utc) + timedelta(hours=2)
        events = [_timed_event(f"Event {i}", base + timedelta(hours=i)) for i in range(25)]

        def fake_create(**kwargs):
            # Score nothing -- forces the padding-with-unscored-events path, which is what
            # actually exercises the raised cap end to end.
            resp = MagicMock()
            resp.choices = [MagicMock(message=MagicMock(content=json.dumps({"results": []})))]
            return resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("routers.discovery.gcal_lib.fetch_events", return_value=events), \
             patch("routers.discovery.llm_client", return_value=mock_client):
            client.get("/api/discovery/events")
            res = client.get("/api/discovery/events")

        assert res.status_code == 200
        assert len(res.json()) == discovery._DISPLAY_RESULT_CAP
