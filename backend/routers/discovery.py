"""Event discovery: public iCal feeds + LLM-based ranking against user interests."""

import concurrent.futures
import hashlib
import html
import json
import re
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Request, Response
from sqlalchemy.orm import Session

import gcal as gcal_lib
import models
import schemas
import app_setting_keys as setting_keys
from deps import LLM_MODEL, get_db, llm_client, local_date

router = APIRouter()

# ── Cache config ──────────────────────────────────────────────────────────────

# iCal feeds are re-fetched at most every 3 hours (stale cache served on error).
_ICAL_CACHE_TTL_SECONDS = 3 * 3600

# A timed event ending within this window of "now" is excluded from discovery results even
# though it hasn't technically ended yet -- it's no longer meaningfully attendable/
# discoverable. See DISCOVERY_IMPROVEMENTS.md's "shows events nearly over" Phase 2.
_NEARLY_OVER_THRESHOLD = timedelta(minutes=60)

# Two events with the same normalized title starting within this window of each other are
# treated as the same real-world event listed by two different feeds (e.g. a city calendar
# and a local "things to do" aggregator both carrying the same farmers market) -- the
# uid-based dedup above only catches literal re-appearances of the same source event, not
# this cross-feed case. See DISCOVERY_IMPROVEMENTS.md's "duplicates" Phase 4.
_DUPLICATE_TIME_WINDOW = timedelta(hours=3)

# LLM ranking results are keyed on a hash of (interests + feedback + event ids).
# No TTL — entries are auto-invalidated when any input changes.
_ranking_cache: dict[str, list] = {}
_RANKING_CACHE_MAX = 64  # evict oldest when over limit

# Max events shown to the user, whether ranked or (no interests configured) plain
# chronological. Raised from 10 -- with Phase 3's batch selection now actually giving
# farther-out events a chance to be scored, a 10-cap was throwing away more of the newly
# fair candidate pool than it needed to. See DISCOVERY_IMPROVEMENTS.md Phase 6.
_DISPLAY_RESULT_CAP = 20

# rkeys with a background ranking task currently running — prevents duplicate
# LLM calls when e.g. React StrictMode double-fires an effect in dev, or the
# user hits refresh again before the first call has finished.
_ranking_inflight: set[str] = set()

# Events sent to the LLM for scoring — kept small so the local model returns
# quickly rather than churning through a large batch.
_RANK_BATCH_SIZE = 12

# Horizon buckets used to select which events make it into the scoring batch, as
# (start_offset_days_inclusive, end_offset_days_exclusive) from "today". Splitting the batch
# across these instead of taking the chronologically-first N events fixes "same-day only"
# discovery at its root: a busy today/tomorrow can no longer crowd out every event further
# out before the LLM ever sees them. See DISCOVERY_IMPROVEMENTS.md Phase 3. A starting
# guess, not a principled answer -- easy to retune once live.
_RANK_HORIZON_BUCKETS = [
    (0, 3),    # next 3 days
    (3, 7),    # rest of this week
    (7, 14),   # next week
    (14, 28),  # weeks 3-4
]


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _serialize_gcal_events(events: list) -> str:
    """JSON-encode raw gcal events, preserving date vs datetime distinction."""
    def _enc(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return {"T": "dt", "v": v.isoformat()}
        if isinstance(v, date_type):
            return {"T": "d", "v": v.isoformat()}
        return v

    return json.dumps([
        {**ev, "start": _enc(ev.get("start")), "end": _enc(ev.get("end"))}
        for ev in events
    ])


def _deserialize_gcal_events(s: str) -> list:
    """Restore gcal events from JSON, reconstructing date/datetime objects."""
    def _dec(v):
        if not isinstance(v, dict) or "T" not in v:
            return v
        if v["T"] == "dt":
            return datetime.fromisoformat(v["v"])
        return date_type.fromisoformat(v["v"])

    return [
        {**ev, "start": _dec(ev.get("start")), "end": _dec(ev.get("end"))}
        for ev in json.loads(s)
    ]


def _ranking_key(interests: str, liked_uids: list, disliked_uids: list, event_ids: list) -> str:
    payload = json.dumps({
        "i": interests,
        "l": sorted(liked_uids),
        "d": sorted(disliked_uids),
        "e": sorted(event_ids),
    })
    return hashlib.sha256(payload.encode()).hexdigest()


def _select_ranking_batch(events: list[dict], today: date_type, batch_size: int) -> list[dict]:
    """Pick up to `batch_size` events for LLM scoring, sampled across horizon buckets instead
    of taking the chronologically-first N -- see DISCOVERY_IMPROVEMENTS.md Phase 3.

    `events` must already be sorted chronologically. Each bucket gets an equal base quota;
    if a bucket has fewer events than its quota, the unused slots roll forward into later
    buckets rather than going to waste, so a busy near-term day can't fill the whole batch
    and starve every farther-out event of ever being scored.
    """
    n = len(_RANK_HORIZON_BUCKETS)
    base_quota = batch_size // n
    remainder = batch_size % n
    quotas = [base_quota + (1 if i < remainder else 0) for i in range(n)]

    buckets: list[list[dict]] = [[] for _ in range(n)]
    for e in events:
        offset_days = (e["start"].date() - today).days
        for i, (lo, hi) in enumerate(_RANK_HORIZON_BUCKETS):
            if lo <= offset_days < hi:
                buckets[i].append(e)
                break
        else:
            if offset_days >= _RANK_HORIZON_BUCKETS[-1][1]:
                buckets[-1].append(e)

    selected: list[dict] = []
    leftover = 0
    for i in range(n):
        take = buckets[i][:quotas[i] + leftover]
        selected.extend(take)
        leftover = quotas[i] + leftover - len(take)

    # Every bucket ran dry before the full batch_size was used -- backfill from whatever's
    # left, in chronological order, so the batch stays as full as the event pool allows.
    if leftover > 0:
        selected_ids = {id(e) for e in selected}
        for e in events:
            if len(selected) >= batch_size:
                break
            if id(e) not in selected_ids:
                selected.append(e)

    selected.sort(key=lambda e: e["start"])
    return selected


_TITLE_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace -- for cross-feed duplicate
    matching, not for display."""
    return " ".join(_TITLE_PUNCT_RE.sub("", title.lower()).split())


def _event_richness(e: dict) -> tuple:
    """Sort key for picking which copy of a detected duplicate to keep -- prefers whichever
    has a URL and, failing a tiebreak there, the longer description."""
    return (1 if e.get("url") else 0, len(e.get("description") or ""))


def _merge_cross_feed_duplicates(events: list[dict]) -> list[dict]:
    """Collapse events that are almost certainly the same real-world listing from two
    different feeds -- same normalized title, starting within _DUPLICATE_TIME_WINDOW of each
    other -- keeping the richer copy. `events` must already be sorted chronologically; the
    window check only needs to look at recently-kept events since anything further back is
    already outside the window. See DISCOVERY_IMPROVEMENTS.md Phase 4."""
    merged: list[dict] = []
    for ev in events:
        norm_title = _normalize_title(ev["title"])
        match_idx = None
        for i in range(len(merged) - 1, -1, -1):
            existing = merged[i]
            if ev["start"] - existing["start"] > _DUPLICATE_TIME_WINDOW:
                break
            if _normalize_title(existing["title"]) == norm_title:
                match_idx = i
                break
        if match_idx is None:
            merged.append(ev)
        elif _event_richness(ev) > _event_richness(merged[match_idx]):
            merged[match_idx] = ev
    return merged


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NEWLINE_RE = re.compile(r"\n{2,}")

def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities for cleaner LLM input."""
    text = _HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _MULTI_NEWLINE_RE.sub("\n", text)
    return text.strip()


_REFINE_SYSTEM = """\
You refine a user's event-interest profile based on their explicit thumbs-up / thumbs-down \
feedback. Update the profile to be more specific and predictive. Keep it concise (2–4 sentences). \
Preserve existing accurate preferences; sharpen or remove ones the feedback contradicts. \
Respond ONLY with the updated profile text — no preamble, no quotes.\
"""


def _refine_interests_bg(liked_titles: list[str], disliked_titles: list[str],
                          current_interests: str) -> None:
    """Background task: ask the LLM to update the interest profile from feedback."""
    if not liked_titles and not disliked_titles:
        return
    parts = [f"Current interest profile:\n{current_interests or '(none yet)'}"]
    if liked_titles:
        parts.append("Events the user LIKED:\n" + "\n".join(f"- {t}" for t in liked_titles))
    if disliked_titles:
        parts.append("Events the user DISLIKED:\n" + "\n".join(f"- {t}" for t in disliked_titles))
    try:
        from database import SessionLocal
        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _REFINE_SYSTEM},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            max_tokens=300,
            temperature=0.4,
        )
        refined = resp.choices[0].message.content.strip()
        # Strip common preamble labels the model may emit despite instructions.
        for prefix in ("Updated interest profile:", "Interest profile:", "Profile:"):
            if refined.lower().startswith(prefix.lower()):
                refined = refined[len(prefix):].strip()
                break
        if not refined:
            return
        db = SessionLocal()
        try:
            row = db.query(models.AppSetting).filter_by(key=setting_keys.DISCOVERY_INTERESTS).first()
            if row:
                row.value = refined
            else:
                db.add(models.AppSetting(key=setting_keys.DISCOVERY_INTERESTS, value=refined))
            db.commit()
        finally:
            db.close()
        _ranking_cache.clear()
        print(f"[discovery] interests refined: {refined[:80]}…")
    except Exception as e:
        print(f"[discovery] interest refinement error: {e}")


def _load_persisted_ranking(db: Session, rkey: str) -> list[dict] | None:
    """Look up a previously-computed ranking from the DB -- populated by _persist_ranking
    below, this is what lets a fresh Cloud Run instance skip re-running the LLM for a rkey
    (same interests + feedback + event ids) some earlier, now-dead instance already scored.
    See DISCOVERY_IMPROVEMENTS.md Phase 5."""
    row = db.query(models.DiscoveryRankingCache).filter_by(rkey=rkey).first()
    if row is None:
        return None
    try:
        return _deserialize_gcal_events(row.results_json)
    except Exception:
        return None


def _persist_ranking(rkey: str, results: list[dict]) -> None:
    """Write a freshly-computed ranking to the DB so it survives a cold start. Uses its own
    session -- called from the background task, same as _refine_interests_bg, so there's no
    request-scoped session available."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        payload = _serialize_gcal_events(results)
        row = db.query(models.DiscoveryRankingCache).filter_by(rkey=rkey).first()
        if row:
            row.results_json = payload
            row.created_at = datetime.now(timezone.utc)
        else:
            db.add(models.DiscoveryRankingCache(rkey=rkey, results_json=payload))
        db.commit()

        # Cap table size the same way the in-memory cache is capped -- rkeys change often
        # (the scoring batch's event ids shift as the window rolls forward day to day), so
        # without a cap this would grow indefinitely.
        count = db.query(models.DiscoveryRankingCache).count()
        if count > _RANKING_CACHE_MAX:
            stale = (
                db.query(models.DiscoveryRankingCache)
                .order_by(models.DiscoveryRankingCache.created_at.asc())
                .limit(count - _RANKING_CACHE_MAX)
                .all()
            )
            for r in stale:
                db.delete(r)
            db.commit()
    finally:
        db.close()


_RANK_SYSTEM = """\
You are a personal event recommender. Given a user's interest description and a list of \
upcoming community events, score each event 1–10 for how well it matches their interests \
(10 = perfect match, 1 = completely irrelevant). Be selective and critical — only return \
events that genuinely align with the user's stated preferences. Generic or loosely related \
events should score low. Aim to surface at most 8–10 standout events from the full list.
Respond ONLY with JSON: {"results": [{"id": "...", "score": 7, "reason": "One sentence why this matches."}]}\
"""


def _rank_events_bg(rkey: str, interests: str, feedback_context: str,
                     ranked_events: list[dict], events: list[dict]) -> None:
    """Background task: call the LLM to rank events and populate the cache.

    Runs after the response has already gone out, so it must not touch the
    request-scoped DB session — everything it needs is passed in as plain
    dicts extracted ahead of time.
    """
    try:
        payload = [
            {
                "id": str(i),
                "title": e["title"],
                "description": _strip_html(e["description"] or "")[:200],
                "date": e["start"].strftime("%a %b %-d"),
                "location": e["location"] or "",
            }
            for i, e in enumerate(ranked_events)
        ]

        client = llm_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _RANK_SYSTEM},
                {"role": "user", "content": (
                    f"User interests: {interests}{feedback_context}\n\n"
                    f"Events:\n{json.dumps(payload)}"
                )},
            ],
            max_tokens=50 * len(ranked_events),
            temperature=0.3,
        )
        scored_map = {
            r["id"]: r
            for r in json.loads(resp.choices[0].message.content).get("results", [])
        }
        for i, ev in enumerate(ranked_events):
            entry = scored_map.get(str(i))
            if entry:
                ev["score"] = entry.get("score")
                ev["reason"] = entry.get("reason")

        # Score picks which events make the cut — highest first, then pad
        # with upcoming unscored events so we always return a full cap's worth —
        # but the final list is displayed in chronological order so same-day
        # events don't jump around relative to each other.
        scored_ids = {id(e) for e in ranked_events if e.get("score") is not None}
        scored_by_rank = sorted(
            [e for e in ranked_events if e.get("score") is not None],
            key=lambda e: (-(e["score"] or 0), e["start"]),
        )
        unscored = [e for e in events if id(e) not in scored_ids]
        selected = (scored_by_rank + unscored)[:_DISPLAY_RESULT_CAP]
        results = sorted(selected, key=lambda e: e["start"])
    except Exception as e:
        print(f"[discovery] LLM ranking error: {e}")
        results = events[:_DISPLAY_RESULT_CAP]

    _ranking_cache[rkey] = results
    if len(_ranking_cache) > _RANKING_CACHE_MAX:
        oldest_key = next(iter(_ranking_cache))
        del _ranking_cache[oldest_key]
    _ranking_inflight.discard(rkey)

    try:
        _persist_ranking(rkey, results)
    except Exception as e:
        print(f"[discovery] ranking cache persist error: {e}")


def _to_dt(v) -> datetime | None:
    """Normalise date or datetime to a timezone-aware datetime."""
    if v is None:
        return None
    if isinstance(v, date_type) and not isinstance(v, datetime):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


# ── CRUD for discovery feeds ───────────────────────────────────────────────

@router.get("/api/discovery/feeds", response_model=List[schemas.DiscoveryFeed])
def get_discovery_feeds(db: Session = Depends(get_db)):
    return db.query(models.EventDiscoveryFeed).all()


@router.put("/api/discovery/feeds")
def set_discovery_feeds(
    feeds: List[schemas.DiscoveryFeed],
    db: Session = Depends(get_db),
):
    db.query(models.EventDiscoveryFeed).delete()
    for f in feeds:
        db.add(models.EventDiscoveryFeed(name=f.name.strip(), ical_url=f.ical_url.strip()))
    db.commit()
    return {"ok": True}


# ── Interest description (stored as an AppSetting) ─────────────────────────

@router.get("/api/discovery/interests")
def get_discovery_interests(db: Session = Depends(get_db)):
    row = db.query(models.AppSetting).filter_by(key=setting_keys.DISCOVERY_INTERESTS).first()
    return {"interests": row.value if row else ""}


@router.put("/api/discovery/interests")
def set_discovery_interests(
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    text = (body.get("interests") or "").strip()
    row = db.query(models.AppSetting).filter_by(key=setting_keys.DISCOVERY_INTERESTS).first()
    if row:
        row.value = text
    else:
        db.add(models.AppSetting(key=setting_keys.DISCOVERY_INTERESTS, value=text))
    db.commit()
    _ranking_cache.clear()
    return {"ok": True}


# ── Feed diagnostics ───────────────────────────────────────────────────────

@router.get("/api/discovery/test-feeds")
def test_discovery_feeds(request: Request, db: Session = Depends(get_db)):
    """Try fetching each configured discovery feed and report success / error."""
    feeds = db.query(models.EventDiscoveryFeed).all()
    today = local_date(request)
    window_end = today + timedelta(days=28)
    results = []
    for feed in feeds:
        try:
            events = gcal_lib.fetch_events(feed.ical_url, today, window_end)
            results.append({
                "id": feed.id,
                "name": feed.name or feed.ical_url,
                "ok": True,
                "event_count": len(events),
                "error": None,
            })
        except Exception as e:
            results.append({
                "id": feed.id,
                "name": feed.name or feed.ical_url,
                "ok": False,
                "event_count": 0,
                "error": str(e),
            })
    return results


# ── Fetch + rank events ────────────────────────────────────────────────────

@router.get("/api/discovery/events", response_model=List[schemas.DiscoveryEventOut])
def get_discovery_events(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    force: bool = False,
):
    feeds = db.query(models.EventDiscoveryFeed).all()
    if not feeds:
        return []

    today = local_date(request)
    window_end = today + timedelta(days=28)
    now = datetime.now(timezone.utc)
    now_naive = datetime.utcnow()
    cache_cutoff = now_naive - timedelta(seconds=_ICAL_CACHE_TTL_SECONDS)

    seen: dict[str, dict] = {}

    # Split into "cache still fresh" (no network call needed) vs. "needs a fetch" up front,
    # then fetch the stale ones CONCURRENTLY -- see DISCOVERY_IMPROVEMENTS.md's "slow to
    # load" Phase 1. This used to be a serial for-loop, so N stale feeds could each block up
    # to the old 45s read timeout one after another; worst case was effectively N × timeout,
    # not just one wait. gcal_lib.fetch_events is pure network I/O with no DB access, so it's
    # safe to run in worker threads -- SQLAlchemy sessions aren't thread-safe, so every DB
    # write (feed.last_fetched/cached_events) still happens back on the main thread, after
    # every fetch has finished, in one batch commit instead of one commit per feed.
    feed_raw_events: dict[int, list] = {}
    feeds_needing_fetch = []
    for feed in feeds:
        if (
            feed.last_fetched is not None
            and feed.cached_events
            and feed.last_fetched > cache_cutoff
        ):
            feed_raw_events[feed.id] = _deserialize_gcal_events(feed.cached_events)
        else:
            feeds_needing_fetch.append(feed)

    if feeds_needing_fetch:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(feeds_needing_fetch))
        ) as pool:
            future_to_feed = {
                pool.submit(gcal_lib.fetch_events, feed.ical_url, today, window_end): feed
                for feed in feeds_needing_fetch
            }
            for future in concurrent.futures.as_completed(future_to_feed):
                feed = future_to_feed[future]
                try:
                    raw_events = future.result()
                    feed.last_fetched = now_naive
                    feed.cached_events = _serialize_gcal_events(raw_events)
                    db.add(feed)
                    feed_raw_events[feed.id] = raw_events
                except Exception as e:
                    print(f"[discovery] feed {feed.id} fetch error: {e}")
                    # Serve stale cache rather than returning nothing for this feed;
                    # if there's no cache at all yet, feed_raw_events just has no entry
                    # for it and the loop below skips it entirely.
                    if feed.cached_events:
                        feed_raw_events[feed.id] = _deserialize_gcal_events(feed.cached_events)
        db.commit()

    for feed in feeds:
        raw_events = feed_raw_events.get(feed.id)
        if raw_events is None:
            continue

        for ev in raw_events:
            start = ev["start"]
            end = ev.get("end")

            # Skip past events -- and, for timed events, ones that are still technically
            # "upcoming" but about to end (DISCOVERY_IMPROVEMENTS.md's "shows events nearly
            # over" Phase 2).
            if ev["all_day"]:
                # DTEND is exclusive per the iCal spec (a 1-day event on Jan 5 has
                # DTEND=Jan6), so a bare `end` date already points at the day AFTER the
                # event's last day. Uses END, not start, deliberately -- a multi-day all-day
                # event that started before today (a 3-day festival, a week-long exhibit)
                # must stay visible until it's actually over, not disappear the moment its
                # start date passes. A feed that omits DTEND entirely means a single-day
                # all-day event, so fall back to start+1 day for that case.
                start_date = start.date() if isinstance(start, datetime) else start
                end_date = (
                    (end.date() if isinstance(end, datetime) else end)
                    if end else start_date + timedelta(days=1)
                )
                if (end_date - today).days <= 0:
                    continue
            elif end:
                # "Nearly over" as well as fully over -- an event ending within the next
                # hour isn't meaningfully attendable/discoverable anymore. Only applies when
                # there's a real end time: extending this same buffer to a start-only event
                # below would risk excluding one that hasn't even started yet, since we have
                # no principled way to know how long an event with no stated end actually
                # lasts. Doesn't penalize a long/multi-day timed event that merely started a
                # while ago, since this only looks at how much time is left before it ends.
                cutoff = _to_dt(end)
                if cutoff and cutoff < now + _NEARLY_OVER_THRESHOLD:
                    continue
            else:
                cutoff = _to_dt(start)
                if cutoff and cutoff < now:
                    continue

            uid = ev.get("uid", "")
            ev_id = f"{feed.id}::{ev['id']}"
            candidate = {
                "id": ev_id,
                "uid": uid,
                "sequence": ev.get("sequence", 0),
                "title": ev["title"],
                "description": ev.get("description"),
                "location": ev.get("location"),
                "url": ev.get("url"),
                "start": _to_dt(start),
                "end": _to_dt(end),
                "all_day": ev["all_day"],
                "feed_name": feed.name or None,
                "score": None,
                "reason": None,
            }

            if uid:
                dedup_key = f"{uid}::{candidate['start'].isoformat()}"
                existing = seen.get(dedup_key)
                if existing is None or candidate["sequence"] >= existing["sequence"]:
                    seen[dedup_key] = candidate
            else:
                seen[ev_id] = candidate

    # Remove events the user explicitly disliked — they shouldn't compete for slots.
    disliked_uids = {
        r.event_uid
        for r in db.query(models.DiscoveryFeedback).filter_by(interested=False).all()
    }
    events = sorted(
        (e for e in seen.values() if not (e["uid"] and e["uid"] in disliked_uids)),
        key=lambda e: e["start"],
    )
    # Collapse the same real-world event listed by two different feeds under different
    # uids (the sort above only catches literal re-appearances of the same source event) --
    # see DISCOVERY_IMPROVEMENTS.md Phase 4.
    events = _merge_cross_feed_duplicates(events)

    # Load interests
    row = db.query(models.AppSetting).filter_by(key=setting_keys.DISCOVERY_INTERESTS).first()
    interests = (row.value or "").strip() if row else ""

    if not interests or not events:
        # No filtering — return chronologically, cap at _DISPLAY_RESULT_CAP
        results = events[:_DISPLAY_RESULT_CAP]
    else:
        # Load feedback for ranking context + cache key
        liked_rows = (
            db.query(models.DiscoveryFeedback)
            .filter_by(interested=True)
            .order_by(models.DiscoveryFeedback.created_at.desc())
            .limit(20).all()
        )
        disliked_rows = (
            db.query(models.DiscoveryFeedback)
            .filter_by(interested=False)
            .order_by(models.DiscoveryFeedback.created_at.desc())
            .limit(20).all()
        )

        ranking_batch = _select_ranking_batch(events, today, _RANK_BATCH_SIZE)

        rkey = _ranking_key(
            interests,
            [r.event_uid for r in liked_rows],
            [r.event_uid for r in disliked_rows],
            [e["id"] for e in ranking_batch],
        )

        if not force and rkey not in _ranking_cache:
            # In-memory miss doesn't necessarily mean this exact ranking has never been
            # computed -- a Cloud Run cold start wipes _ranking_cache but not the DB, so an
            # earlier (now-dead) instance may already have scored this exact rkey. See
            # DISCOVERY_IMPROVEMENTS.md Phase 5.
            persisted = _load_persisted_ranking(db, rkey)
            if persisted is not None:
                _ranking_cache[rkey] = persisted

        if not force and rkey in _ranking_cache:
            results = _ranking_cache[rkey]
            response.headers["X-Ranking-Status"] = "ready"
        else:
            # Show something immediately — the previous ranking for this key
            # if we have one (force refresh), otherwise plain chronological
            # order — while the LLM re-ranks in the background. The client
            # re-fetches shortly after to pick up the ranked result.
            results = _ranking_cache.get(rkey, events[:_DISPLAY_RESULT_CAP])
            response.headers["X-Ranking-Status"] = "pending"

            if rkey not in _ranking_inflight:
                _ranking_inflight.add(rkey)

                feedback_lines = []
                if liked_rows:
                    feedback_lines.append("Events this user previously liked: " +
                                           "; ".join(r.event_title for r in liked_rows))
                if disliked_rows:
                    feedback_lines.append("Events this user did NOT like: " +
                                           "; ".join(r.event_title for r in disliked_rows))
                feedback_context = ("\n\n" + "\n".join(feedback_lines)) if feedback_lines else ""

                # Send a small batch of events using short numeric ids to
                # prevent the model from hallucinating opaque composite id
                # strings, and to keep the local model's response quick.
                # The batch itself is sampled across horizon buckets, not just the
                # chronologically-first N -- see _select_ranking_batch.
                background_tasks.add_task(
                    _rank_events_bg, rkey, interests, feedback_context,
                    ranking_batch, events,
                )

    return [
        schemas.DiscoveryEventOut(**{k: v for k, v in e.items() if k != "sequence"})
        for e in results
    ]


# ── User feedback (like / dislike) ─────────────────────────────────────────

@router.get("/api/discovery/feedback")
def get_feedback(db: Session = Depends(get_db)):
    rows = db.query(models.DiscoveryFeedback).order_by(models.DiscoveryFeedback.created_at.desc()).all()
    return [{"event_uid": r.event_uid, "event_title": r.event_title, "interested": r.interested} for r in rows]


@router.post("/api/discovery/feedback")
def save_feedback(
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    event_uid = (body.get("event_uid") or "").strip()
    if not event_uid:
        return {"ok": False, "error": "event_uid required"}
    event_title = (body.get("event_title") or "").strip()
    event_description = (body.get("event_description") or "").strip() or None
    interested = bool(body.get("interested"))

    row = db.query(models.DiscoveryFeedback).filter_by(event_uid=event_uid).first()
    if row:
        row.interested = interested
    else:
        db.add(models.DiscoveryFeedback(
            event_uid=event_uid,
            event_title=event_title,
            event_description=event_description,
            interested=interested,
        ))
    db.commit()
    _ranking_cache.clear()

    # Refine the interest profile in the background so the next ranking call
    # benefits from the updated preferences.
    all_feedback = db.query(models.DiscoveryFeedback).all()
    liked_titles    = [r.event_title for r in all_feedback if r.interested]
    disliked_titles = [r.event_title for r in all_feedback if not r.interested]
    interests_row = db.query(models.AppSetting).filter_by(key=setting_keys.DISCOVERY_INTERESTS).first()
    current_interests = (interests_row.value or "").strip() if interests_row else ""
    background_tasks.add_task(
        _refine_interests_bg, liked_titles, disliked_titles, current_interests
    )

    return {"ok": True}
