"""Event discovery: public iCal feeds + LLM-based ranking against user interests."""

import hashlib
import json
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

import gcal as gcal_lib
import models
import schemas
from deps import LLM_MODEL, get_db, llm_client, local_date

router = APIRouter()

# ── Cache config ──────────────────────────────────────────────────────────────

# iCal feeds are re-fetched at most every 3 hours (stale cache served on error).
_ICAL_CACHE_TTL_SECONDS = 3 * 3600

# LLM ranking results are keyed on a hash of (interests + feedback + event ids).
# No TTL — entries are auto-invalidated when any input changes.
_ranking_cache: dict[str, list] = {}
_RANKING_CACHE_MAX = 64  # evict oldest when over limit


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

_RANK_SYSTEM = """\
You are a personal event recommender. Given a user's interest description and a list of \
upcoming community events, score each event 1–10 for how well it matches their interests \
(10 = perfect match, 1 = completely irrelevant). Be selective and critical — only return \
events that genuinely align with the user's stated preferences. Generic or loosely related \
events should score low. Aim to surface at most 8–10 standout events from the full list.
Respond ONLY with JSON: {"results": [{"id": "...", "score": 7, "reason": "One sentence why this matches."}]}\
"""


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
    row = db.query(models.AppSetting).filter_by(key="event_discovery_interests").first()
    return {"interests": row.value if row else ""}


@router.put("/api/discovery/interests")
def set_discovery_interests(
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    text = (body.get("interests") or "").strip()
    row = db.query(models.AppSetting).filter_by(key="event_discovery_interests").first()
    if row:
        row.value = text
    else:
        db.add(models.AppSetting(key="event_discovery_interests", value=text))
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
def get_discovery_events(request: Request, db: Session = Depends(get_db)):
    feeds = db.query(models.EventDiscoveryFeed).all()
    if not feeds:
        return []

    today = local_date(request)
    window_end = today + timedelta(days=28)
    now = datetime.now(timezone.utc)
    now_naive = datetime.utcnow()
    cache_cutoff = now_naive - timedelta(seconds=_ICAL_CACHE_TTL_SECONDS)

    seen: dict[str, dict] = {}

    for feed in feeds:
        # Use DB-cached events if they're still fresh (< 3h old).
        if (
            feed.last_fetched is not None
            and feed.cached_events
            and feed.last_fetched > cache_cutoff
        ):
            raw_events = _deserialize_gcal_events(feed.cached_events)
        else:
            try:
                raw_events = gcal_lib.fetch_events(feed.ical_url, today, window_end)
                feed.last_fetched = now_naive
                feed.cached_events = _serialize_gcal_events(raw_events)
                db.add(feed)
                db.commit()
            except Exception as e:
                print(f"[discovery] feed {feed.id} fetch error: {e}")
                # Serve stale cache rather than returning nothing
                if feed.cached_events:
                    raw_events = _deserialize_gcal_events(feed.cached_events)
                else:
                    continue

        for ev in raw_events:
            start = ev["start"]
            end = ev.get("end")

            # Skip past events
            if ev["all_day"]:
                start_date = start.date() if isinstance(start, datetime) else start
                if (start_date - today).days < 0:
                    continue
            else:
                cutoff = _to_dt(end if end else start)
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

    events = sorted(seen.values(), key=lambda e: e["start"])

    # Load interests
    row = db.query(models.AppSetting).filter_by(key="event_discovery_interests").first()
    interests = (row.value or "").strip() if row else ""

    if not interests or not events:
        # No filtering — return chronologically, cap at 10
        results = events[:10]
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

        rkey = _ranking_key(
            interests,
            [r.event_uid for r in liked_rows],
            [r.event_uid for r in disliked_rows],
            [e["id"] for e in events[:60]],
        )

        if rkey in _ranking_cache:
            results = _ranking_cache[rkey]
        else:
            # LLM ranking: send up to 60 events, get back scored subset
            payload = [
                {
                    "id": e["id"],
                    "title": e["title"],
                    "description": (e["description"] or "")[:300],
                    "date": e["start"].strftime("%a %b %-d"),
                    "location": e["location"] or "",
                }
                for e in events[:60]
            ]

            feedback_lines = []
            if liked_rows:
                feedback_lines.append("Events this user previously liked: " +
                                       "; ".join(r.event_title for r in liked_rows))
            if disliked_rows:
                feedback_lines.append("Events this user did NOT like: " +
                                       "; ".join(r.event_title for r in disliked_rows))
            feedback_context = ("\n\n" + "\n".join(feedback_lines)) if feedback_lines else ""

            try:
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
                    max_tokens=2000,
                    temperature=0.3,
                )
                scored_map = {
                    r["id"]: r
                    for r in json.loads(resp.choices[0].message.content).get("results", [])
                }
                for ev in events:
                    if ev["id"] in scored_map:
                        ev["score"] = scored_map[ev["id"]].get("score")
                        ev["reason"] = scored_map[ev["id"]].get("reason")

                results = sorted(
                    [e for e in events if (e.get("score") or 0) >= 7],
                    key=lambda e: (-(e["score"] or 0), e["start"]),
                )[:10]
            except Exception as e:
                print(f"[discovery] LLM ranking error: {e}")
                results = events[:10]

            # Store in ranking cache; evict oldest entries if over limit
            _ranking_cache[rkey] = results
            if len(_ranking_cache) > _RANKING_CACHE_MAX:
                oldest_key = next(iter(_ranking_cache))
                del _ranking_cache[oldest_key]

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
def save_feedback(body: dict = Body(...), db: Session = Depends(get_db)):
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
    return {"ok": True}
