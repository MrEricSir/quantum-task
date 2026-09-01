"""
Withings OAuth + data sync router.

OAuth flow:
  1. GET /api/withings/auth-url  → frontend opens returned URL in new tab
  2. User authorises in Withings; Withings redirects to WITHINGS_CALLBACK_URI
  3. GET /api/withings/callback  → exchanges code for tokens, stores credentials,
                                    redirects browser to {ALLOWED_ORIGIN}/health
  4. POST /api/withings/sync     → manual or scheduled sync
  5. GET /api/withings/health-data → measurements + per-habit completion history

Credentials are stored in the withings_credentials table (typed columns).
Last-sync timestamp is stored in the last_synced column on that same row.
"""

import hmac
import json
import os
import secrets
import threading
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import List
from urllib.parse import parse_qs, urlparse

import arrow
import requests as _requests
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

import app_setting_keys as setting_keys
import models
import schemas
from deps import get_db
from integrations.base import Measurement
from streak import recompute_from

router = APIRouter()

WITHINGS_CLIENT_ID = os.getenv("WITHINGS_CLIENT_ID", "")
WITHINGS_SECRET = os.getenv("WITHINGS_SECRET", "")
WITHINGS_CALLBACK_URI = os.getenv(
    "WITHINGS_CALLBACK_URI", "http://localhost:8000/api/withings/callback"
)
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:5173")

METRICS = {"steps", "fat_ratio", "weight"}


# ── Credential helpers ────────────────────────────────────────────────────────

def _save_credentials_from_dict(db: Session, data: dict) -> None:
    """Persist a credentials dict to the WithingsCredentials table (upsert)."""
    row = db.query(models.WithingsCredentials).first()
    if row is None:
        row = models.WithingsCredentials()
        db.add(row)
    row.access_token    = data["access_token"]
    row.token_type      = data.get("token_type", "Bearer")
    row.refresh_token   = data["refresh_token"]
    row.userid          = int(data["userid"])
    row.client_id       = data.get("client_id", "")
    row.consumer_secret = data.get("consumer_secret", "")
    row.expires_in      = int(data.get("expires_in", 10800))
    db.commit()


def _save_credentials(db: Session, creds) -> None:
    """Persist a Credentials2 object to the WithingsCredentials table."""
    _save_credentials_from_dict(db, {
        "access_token":    creds.access_token,
        "token_type":      creds.token_type,
        "refresh_token":   creds.refresh_token,
        "userid":          creds.userid,
        "client_id":       creds.client_id,
        "consumer_secret": creds.consumer_secret,
        "expires_in":      creds.expires_in,
    })


def _load_credentials_dict(db: Session) -> dict | None:
    """Load credentials as a plain dict for use in API calls, or None if not connected."""
    row = db.query(models.WithingsCredentials).first()
    if not row:
        return None
    return {
        "access_token":    row.access_token,
        "token_type":      row.token_type,
        "refresh_token":   row.refresh_token,
        "userid":          row.userid,
        "client_id":       row.client_id,
        "consumer_secret": row.consumer_secret,
        "expires_in":      row.expires_in,
    }


def _load_credentials(db: Session):
    """Load stored Credentials2, or None if not connected."""
    from withings_api.common import Credentials2
    row = db.query(models.WithingsCredentials).first()
    if not row:
        return None
    try:
        return Credentials2(
            access_token=row.access_token,
            token_type=row.token_type,
            refresh_token=row.refresh_token,
            userid=row.userid,
            client_id=row.client_id,
            consumer_secret=row.consumer_secret,
            expires_in=row.expires_in,
            # 'created' uses ArrowType (pydantic v1) which conflicts with pydantic v2;
            # omit it so it defaults to arrow.utcnow() — fine since tokens auto-refresh.
        )
    except Exception:
        return None


# ── Sync logic ────────────────────────────────────────────────────────────────

def upsert_measurement(
    db: Session, date_str: str, metric: str, value: float, source: str = "withings"
) -> models.WithingsMeasurement:
    """Insert or update the (date, metric) row. `source` is overwritten on every write --
    whichever caller wrote most recently (device sync or manual entry) is the current source
    of truth for that reading; see migration 00038 for why that's the intended behavior."""
    existing = db.query(models.WithingsMeasurement).filter_by(
        date=date_str, metric=metric
    ).first()
    if existing:
        existing.value = value
        existing.source = source
        existing.synced_at = datetime.now(timezone.utc)
        return existing
    else:
        row = models.WithingsMeasurement(
            date=date_str,
            metric=metric,
            value=value,
            source=source,
            synced_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.flush()  # make visible to subsequent queries in same transaction, populates row.id
        return row


_AUTO_CHECK_LOOKBACK_DAYS = 3  # re-check this many trailing days, not just today


def _auto_check_habits(db: Session, today: date, lookback_days: int = _AUTO_CHECK_LOOKBACK_DAYS) -> None:
    """Auto-complete habits whose Withings goal was met on any of the last
    `lookback_days` days (today included).

    A single sync fetches and upserts measurements for every day in its
    fetch window, but a device's activity data can finalize or get revised
    after the fact -- e.g. a watch that only uploads a day's full step count
    after local midnight. If we only ever re-checked "today", a goal that
    was actually met would be missed forever the moment the day rolls over
    before the corrected measurement arrives. Re-checking a short trailing
    window on every sync catches that without needing to know why a given
    day's data arrived late.
    """
    for days_back in range(lookback_days):
        auto_check_habits_for_date(db, today - timedelta(days=days_back), today)


def auto_check_habits_for_date(db: Session, check_date: date, today: date) -> None:
    """Auto-complete habits whose Withings goal was met on `check_date`.

    Steps: goal met when value >= goal.
    Fat ratio / weight: goal met when value <= goal (lower is better).
    """
    date_str = check_date.isoformat()
    for metric in METRICS:
        row = db.query(models.WithingsMeasurement).filter_by(
            date=date_str, metric=metric
        ).first()
        if not row:
            continue
        linked = (
            db.query(models.Habit)
            .filter(
                models.Habit.health_metric == metric,
                models.Habit.health_goal.isnot(None),
                models.Habit.archived == False,  # noqa: E712
            )
            .all()
        )
        for habit in linked:
            met = (row.value >= habit.health_goal) if metric == "steps" else (row.value <= habit.health_goal)
            if met and not db.query(models.HabitCompletion).filter_by(
                habit_id=habit.id, date=date_str
            ).first():
                db.add(models.HabitCompletion(habit_id=habit.id, date=date_str))
                db.flush()
                recompute_from(db, habit.id, check_date, today=today)


class _TokenAuthError(Exception):
    """Raised when Withings explicitly rejects the OAuth token (must reconnect)."""


def _withings_get(creds_data: dict, path: str, params: dict) -> dict:
    """Make an authenticated GET request to the Withings API."""
    resp = _requests.get(
        f"https://wbsapi.withings.net/{path}",
        params=params,
        headers={"Authorization": f"Bearer {creds_data['access_token']}"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != 0:
        raise RuntimeError(f"Withings API error status={body.get('status')}: {body.get('error', '')}")
    return body.get("body", {})


# ── Pure API fetch (no DB access) ───────────────────────────────────────────────
# Withings numeric measurement-type codes -> canonical metric name + rounding precision.
_MEASURE_TYPE_MAP = {
    6:  ("fat_ratio",    2),  # FAT_RATIO
    1:  ("weight",       2),  # WEIGHT (kg)
    9:  ("bp_diastolic", 1),  # DIASTOLIC BP (mmHg)
    10: ("bp_systolic",  1),  # SYSTOLIC BP (mmHg)
    11: ("heart_rate",   1),  # HEART RATE (bpm)
    54: ("spo2",         1),  # SPO2 (%)
}


def _fetch_steps(creds_data: dict, start: date, end: date) -> list[Measurement]:
    body = _withings_get(creds_data, "v2/measure", {
        "action": "getactivity",
        "data_fields": "steps",
        "startdateymd": start.isoformat(),
        "enddateymd": end.isoformat(),
    })
    return [
        Measurement(date=item["date"], value=float(item["steps"]))
        for item in body.get("activities", [])
        if item.get("steps") is not None
    ]


def _fetch_body_measurements(creds_data: dict, start: date, end: date) -> dict[str, list[Measurement]]:
    body = _withings_get(creds_data, "measure", {
        "action": "getmeas",
        "startdate": int(datetime.combine(start, datetime.min.time()).timestamp()),
        "enddate": int(datetime.combine(end + timedelta(days=1), datetime.min.time()).timestamp()),
    })
    readings: dict[str, list[Measurement]] = {}
    seen_types: set[int] = set()
    for group in body.get("measuregrps", []):
        grp_date = date.fromtimestamp(group["date"]).isoformat()
        for measure in group.get("measures", []):
            t = measure.get("type")
            seen_types.add(t)
            mapped = _MEASURE_TYPE_MAP.get(t)
            if not mapped:
                continue
            metric, precision = mapped
            raw = measure["value"] * (10 ** measure["unit"])
            readings.setdefault(metric, []).append(Measurement(date=grp_date, value=round(raw, precision)))
    # Log which measurement types came back to help diagnose missing data
    print(f"[withings] getmeas returned types: {sorted(seen_types)}", flush=True)
    return readings


def _fetch_sleep(creds_data: dict, start: date, end: date) -> dict[str, list[Measurement]]:
    body = _withings_get(creds_data, "v2/sleep", {
        "action": "getsummary",
        "startdateymd": start.isoformat(),
        "enddateymd": end.isoformat(),
        "data_fields": "sleep_score,total_sleep_time,deep_sleep_duration,spo2_average",
    })
    readings: dict[str, list[Measurement]] = {}
    for item in body.get("series", []):
        d = item.get("date")
        if not d:
            continue
        data = item.get("data", {})
        if data.get("sleep_score") is not None:
            readings.setdefault("sleep_score", []).append(Measurement(date=d, value=float(data["sleep_score"])))
        if data.get("total_sleep_time") is not None:
            readings.setdefault("sleep_minutes", []).append(Measurement(date=d, value=round(float(data["total_sleep_time"]), 0)))
        if data.get("deep_sleep_duration") is not None:
            readings.setdefault("sleep_deep_minutes", []).append(Measurement(date=d, value=round(float(data["deep_sleep_duration"]), 0)))
        if data.get("spo2_average") is not None:
            readings.setdefault("spo2", []).append(Measurement(date=d, value=round(float(data["spo2_average"]), 1)))
    return readings


def fetch_measurements(creds_data: dict, start: date, end: date) -> tuple[dict[str, list[Measurement]], dict[str, str]]:
    """Pure fetch from the Withings API -- no DB access, no persistence.

    Returns (canonical metric name -> readings, section name -> error message for any of the
    three sections that failed; a failed section is simply absent from readings, the other
    two still populate normally).

    Used by both do_sync() below (which upserts the result) and
    integrations.withings.WithingsProvider.sync() (which returns it as-is, per the
    HealthProvider interface) -- one implementation of "call the Withings API and parse a
    reading" instead of two, per Part 1's "shared function, not two implementations" rule.
    """
    readings: dict[str, list[Measurement]] = {}
    errors: dict[str, str] = {}

    try:
        readings["steps"] = _fetch_steps(creds_data, start, end)
    except Exception as exc:
        print(f"[withings] activity sync error: {exc}", flush=True)
        errors["activity"] = str(exc)

    try:
        for metric, points in _fetch_body_measurements(creds_data, start, end).items():
            readings.setdefault(metric, []).extend(points)
    except Exception as exc:
        print(f"[withings] measurements sync error: {exc}", flush=True)
        errors["measurements"] = str(exc)

    try:
        for metric, points in _fetch_sleep(creds_data, start, end).items():
            readings.setdefault(metric, []).extend(points)
    except Exception as exc:
        print(f"[withings] sleep sync error: {exc}", flush=True)
        errors["sleep"] = str(exc)

    return readings, errors


def _refresh_token(creds_data: dict, db: Session) -> dict:
    """Exchange a refresh token for a new access token and persist it."""
    resp = _requests.post(
        "https://wbsapi.withings.net/v2/oauth2",
        data={
            "action": "requesttoken",
            "grant_type": "refresh_token",
            "client_id": WITHINGS_CLIENT_ID,
            "client_secret": WITHINGS_SECRET,
            "refresh_token": creds_data["refresh_token"],
        },
        timeout=30,
    )
    # HTTP 401/403 = token explicitly rejected by server
    if resp.status_code in (401, 403):
        raise _TokenAuthError(f"Token rejected: HTTP {resp.status_code}")
    resp.raise_for_status()
    payload = resp.json()
    status_code = payload.get("status", 0)
    if status_code != 0:
        # Withings status 401 = invalid_token, 293 = access_token_expired, etc.
        # Treat any OAuth/auth status as a hard auth failure; other errors are transient.
        _AUTH_STATUSES = {401, 293, 342, 343}
        if status_code in _AUTH_STATUSES:
            raise _TokenAuthError(f"Token refresh failed: status={status_code} {payload.get('error', '')}")
        raise RuntimeError(f"Token refresh failed: status={status_code} {payload.get('error', '')}")
    body = payload["body"]
    new_creds = {
        **creds_data,
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_in": body.get("expires_in", 10800),
    }
    _save_credentials_from_dict(db, new_creds)
    return new_creds


def _notify_reauth_needed_once(db: Session) -> None:
    """Send a Telegram notification the first time a hard auth failure
    happens, not on every subsequent hourly/2h retry until the user
    reconnects — that would be a message every 1-2 hours indefinitely.
    Deliberately doesn't care whether Telegram is even configured; the
    caller-agnostic dedup flag is still meaningful either way."""
    import app_setting_keys as setting_keys
    from settings import Settings
    s = Settings(db)
    if s.get(setting_keys.WITHINGS_AUTH_FAILURE_NOTIFIED) == "1":
        return
    s.set(setting_keys.WITHINGS_AUTH_FAILURE_NOTIFIED, "1")
    if s.telegram_token and s.telegram_chat_id:
        from telegram.scheduler import notify_withings_reauth_needed
        notify_withings_reauth_needed(s.telegram_token, s.telegram_chat_id)


def _clear_reauth_notified(db: Session) -> None:
    """Called after any successful refresh -- resets the dedup flag so a
    future failure (e.g. after the user reconnects and it breaks again
    later) notifies again instead of staying permanently silent."""
    import app_setting_keys as setting_keys
    from settings import Settings
    Settings(db).set(setting_keys.WITHINGS_AUTH_FAILURE_NOTIFIED, "0")


_sync_lock = threading.Lock()


def do_sync(db: Session) -> dict:
    """Serializes calls to _do_sync_impl -- see its docstring for why. A
    second concurrent call returns immediately (non-blocking acquire)
    rather than waiting: syncs are frequent and cheap enough that skipping
    one when another is already running is strictly better than queuing up
    behind it."""
    if not _sync_lock.acquire(blocking=False):
        print("[withings] sync already in progress — skipping concurrent call", flush=True)
        return {"ok": False, "error": "sync_in_progress"}
    try:
        return _do_sync_impl(db)
    finally:
        _sync_lock.release()


def _do_sync_impl(db: Session) -> dict:
    """Fetch recent Withings data and upsert into withings_measurements.
    Returns a summary dict.

    Must only ever run one at a time -- see do_sync()'s lock. Withings
    rotates the refresh token on every use (the old one is invalidated the
    moment a new one is issued), so two concurrent calls both reading the
    same stored token and both trying to refresh it will corrupt it: one
    redeems an already-spent token. Confirmed in production logs: a
    "Same arguments in less than 10 seconds" rejection (Withings' own
    anti-replay check) followed by a multi-day stretch of
    "invalid_refresh_token" failures recurring every ~2 hours, matching
    the in-process scheduler's cadence, until a manual reconnect recovered
    it. The two real trigger sources that were racing: main.py's
    in-process 2-hour loop and the hourly Cloud Scheduler job, both
    calling do_sync() completely independently of each other."""
    creds_data = _load_credentials_dict(db)
    if not creds_data:
        return {"ok": False, "error": "not_connected"}

    # Proactively refresh the access token before syncing.
    # Withings tokens expire after ~3 hours; refresh ensures we always have a
    # valid token. If refresh itself fails, the token has been revoked and the
    # user must reconnect.
    if WITHINGS_CLIENT_ID and WITHINGS_SECRET:
        try:
            creds_data = _refresh_token(creds_data, db)
        except _TokenAuthError as exc:
            print(f"[withings] token rejected (reconnect required): {exc}", flush=True)
            _notify_reauth_needed_once(db)
            db.commit()  # persist the dedup flag -- this path returns immediately, no later commit to ride along with
            return {"ok": False, "error": "invalid_token"}
        except Exception as exc:
            print(f"[withings] token refresh transient error: {exc}", flush=True)
            return {"ok": False, "error": "sync_failed"}
        else:
            _clear_reauth_notified(db)

    # Use the user's local date (via stored tz offset) — the server runs UTC on Cloud Run.
    tz_offset = 0
    try:
        import app_setting_keys as _sk
        row = db.query(models.AppSetting).filter_by(key=_sk.BRIEFING_TZ_OFFSET).first()
        if row and row.value:
            tz_offset = int(row.value)
    except Exception:
        pass
    today = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)).date()
    start = today - timedelta(days=89)
    synced = {"steps": 0, "fat_ratio": 0, "weight": 0, "bp_systolic": 0, "bp_diastolic": 0, "heart_rate": 0, "spo2": 0, "sleep_score": 0, "sleep_minutes": 0, "sleep_deep_minutes": 0}

    # Requires USER_SLEEP_EVENTS scope for the sleep portion; silently skipped if not granted
    # (fetch_measurements catches that section's failure independently of the other two).
    readings, errors = fetch_measurements(creds_data, start, today)
    for metric, points in readings.items():
        for m in points:
            upsert_measurement(db, m.date, metric, m.value)
            synced[metric] = synced.get(metric, 0) + 1

    db.commit()
    _auto_check_habits(db, today)
    db.commit()

    creds_row = db.query(models.WithingsCredentials).first()
    if creds_row:
        creds_row.last_synced = datetime.now(timezone.utc)
        db.commit()

    result: dict = {"ok": True, "synced": synced}
    if errors:
        result["errors"] = errors
    return result


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/withings/status", response_model=schemas.WithingsStatus)
def withings_status(db: Session = Depends(get_db)):
    row = db.query(models.WithingsCredentials).first()
    # last_synced is stored as a naive datetime that represents UTC by
    # convention (see models.py's date/time storage docstring). Attach that
    # tzinfo explicitly before serializing -- otherwise the frontend has no
    # way to know the string is UTC and `new Date(...)` parses it as local
    # time, showing the wrong clock time.
    last_synced = None
    if row and row.last_synced:
        last_synced = row.last_synced.replace(tzinfo=timezone.utc).isoformat()
    return schemas.WithingsStatus(
        connected=row is not None,
        last_synced=last_synced,
    )


def _store_pending_oauth_state(db: Session, state: str) -> None:
    row = db.query(models.AppSetting).filter_by(key=setting_keys.WITHINGS_OAUTH_STATE).first()
    if row:
        row.value = state
    else:
        db.add(models.AppSetting(key=setting_keys.WITHINGS_OAUTH_STATE, value=state))
    db.commit()


def _pop_pending_oauth_state(db: Session) -> str:
    """Return the pending state value and clear it (one-time use)."""
    row = db.query(models.AppSetting).filter_by(key=setting_keys.WITHINGS_OAUTH_STATE).first()
    if not row:
        return ""
    value = row.value
    db.delete(row)
    db.commit()
    return value


def get_auth_url(db: Session) -> str:
    """Build the Withings OAuth authorization URL. Raises RuntimeError if not configured.

    Shared by the /auth-url endpoint below and integrations.withings.WithingsProvider.

    Generates and persists a random CSRF `state` value that /callback below must see come
    back unchanged before it will exchange the code -- otherwise an attacker could trick the
    user's browser into completing an authorization the user never initiated (linking the
    attacker's own Withings account to this app, so their data flows in as if it were the
    user's).
    """
    from withings_api import WithingsAuth, AuthScope
    if not WITHINGS_CLIENT_ID or not WITHINGS_SECRET:
        raise RuntimeError("Withings credentials not configured")
    auth = WithingsAuth(
        client_id=WITHINGS_CLIENT_ID,
        consumer_secret=WITHINGS_SECRET,
        callback_uri=WITHINGS_CALLBACK_URI,
        scope=(AuthScope.USER_METRICS, AuthScope.USER_ACTIVITY, AuthScope.USER_SLEEP_EVENTS),
    )
    url = auth.get_authorize_url()
    state = parse_qs(urlparse(url).query).get("state", [""])[0]
    _store_pending_oauth_state(db, state)
    return url


@router.get("/api/withings/auth-url")
def withings_auth_url(db: Session = Depends(get_db)):
    """Return the Withings OAuth authorization URL."""
    try:
        return {"url": get_auth_url(db)}
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


def exchange_code(code: str, db: Session) -> dict:
    """Exchange an OAuth authorization code for tokens and persist them. Raises on failure.

    Shared by the /callback endpoint below and integrations.withings.WithingsProvider.
    """
    from withings_api.common import Credentials2
    # Exchange code directly — avoids requests_oauthlib state validation
    # and the adjust_withings_token bug that masks real error codes.
    resp = _requests.post(
        "https://wbsapi.withings.net/v2/oauth2",
        data={
            "action": "requesttoken",
            "grant_type": "authorization_code",
            "client_id": WITHINGS_CLIENT_ID,
            "client_secret": WITHINGS_SECRET,
            "code": code,
            "redirect_uri": WITHINGS_CALLBACK_URI,
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    print(f"[withings] token response status={payload.get('status')} body_keys={list((payload.get('body') or {}).keys())}")
    if payload.get("status") != 0:
        err = payload.get("error") or f"status={payload.get('status')}"
        raise RuntimeError(f"Withings token error: {err}")
    body = payload["body"]
    creds = Credentials2(
        access_token=body["access_token"],
        token_type=body.get("token_type", "Bearer"),
        refresh_token=body["refresh_token"],
        userid=int(body["userid"]),
        client_id=WITHINGS_CLIENT_ID,
        consumer_secret=WITHINGS_SECRET,
        expires_in=int(body.get("expires_in", 10800)),
    )
    _save_credentials(db, creds)
    return _load_credentials_dict(db)


@router.get("/api/withings/callback")
def withings_callback(code: str, state: str = "", db: Session = Depends(get_db)):
    """OAuth callback: exchange authorization code for tokens."""
    expected_state = _pop_pending_oauth_state(db)
    if not expected_state or not secrets.compare_digest(state, expected_state):
        print("[withings] callback error: state mismatch or missing (possible CSRF attempt)")
        return RedirectResponse(f"{ALLOWED_ORIGIN}/board?withings=error&msg=Invalid+or+expired+authorization+attempt")
    try:
        exchange_code(code, db)
        # Do NOT call do_sync here — it makes 4 sequential Withings API calls
        # and can exceed Cloud Run's request timeout, killing the redirect response.
        # The frontend triggers a sync automatically after detecting ?withings=connected.
    except Exception as exc:
        traceback.print_exc()
        print(f"[withings] callback error: {exc}")
        return RedirectResponse(f"{ALLOWED_ORIGIN}/board?withings=error&msg={exc}")
    return RedirectResponse(f"{ALLOWED_ORIGIN}/board?withings=connected")


@router.post("/api/withings/sync")
def withings_sync(db: Session = Depends(get_db)):
    """Manually trigger a Withings data sync."""
    return do_sync(db)


_GOALS_KEY = setting_keys.WITHINGS_HEALTH_GOALS
_ALL_METRICS = ("steps", "fat_ratio", "weight")


def _load_goals(db: Session) -> dict:
    row = db.query(models.AppSetting).filter_by(key=_GOALS_KEY).first()
    if not row:
        return {m: None for m in _ALL_METRICS}
    try:
        data = json.loads(row.value)
        return {m: data.get(m) for m in _ALL_METRICS}
    except Exception:
        return {m: None for m in _ALL_METRICS}


@router.get("/api/withings/goals")
def withings_get_goals(db: Session = Depends(get_db)):
    return _load_goals(db)


@router.patch("/api/withings/goals")
def withings_set_goals(payload: dict, db: Session = Depends(get_db)):
    goals = _load_goals(db)
    for metric in _ALL_METRICS:
        if metric in payload:
            val = payload[metric]
            goals[metric] = float(val) if val is not None else None
    db.merge(models.AppSetting(key=_GOALS_KEY, value=json.dumps(goals)))
    db.commit()
    return goals


@router.delete("/api/withings/disconnect")
def withings_disconnect(db: Session = Depends(get_db)):
    """Remove stored Withings credentials."""
    db.query(models.WithingsCredentials).delete()
    db.commit()
    return {"ok": True}


@router.get("/api/withings/health-data", response_model=schemas.WithingsHealthData)
def withings_health_data(days: int = 90, db: Session = Depends(get_db)):
    """Return stored measurements + habit completion history for all Withings-linked habits."""
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()

    measurements = (
        db.query(models.WithingsMeasurement)
        .filter(models.WithingsMeasurement.date >= cutoff)
        .order_by(models.WithingsMeasurement.date)
        .all()
    )

    # Only fetch completion history for habits that have a health_metric
    linked_habits = (
        db.query(models.Habit)
        .filter(
            models.Habit.health_metric.isnot(None),
            models.Habit.archived == False,  # noqa: E712
        )
        .all()
    )

    habit_completions: dict[str, List[str]] = {}
    for habit in linked_habits:
        completions = (
            db.query(models.HabitCompletion)
            .filter(
                models.HabitCompletion.habit_id == habit.id,
                models.HabitCompletion.date >= cutoff,
            )
            .all()
        )
        habit_completions[str(habit.id)] = [c.date for c in completions]

    return schemas.WithingsHealthData(
        measurements=[
            schemas.WithingsMeasurementOut(id=m.id, date=m.date, metric=m.metric, value=m.value, source=m.source)
            for m in measurements
        ],
        habit_completions=habit_completions,
    )
