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

import json
import os
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import List

import arrow
import requests as _requests
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db
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

def _upsert_measurement(db: Session, date_str: str, metric: str, value: float) -> None:
    existing = db.query(models.WithingsMeasurement).filter_by(
        date=date_str, metric=metric
    ).first()
    if existing:
        existing.value = value
        existing.synced_at = datetime.now(timezone.utc)
    else:
        db.add(models.WithingsMeasurement(
            date=date_str,
            metric=metric,
            value=value,
            synced_at=datetime.now(timezone.utc),
        ))
        db.flush()  # make visible to subsequent queries in same transaction


def _auto_check_habits(db: Session, today: date) -> None:
    """Auto-complete habits whose Withings goal was met today.

    Steps: goal met when value >= goal.
    Fat ratio / weight: goal met when value <= goal (lower is better).
    """
    today_str = today.isoformat()
    for metric in METRICS:
        row = db.query(models.WithingsMeasurement).filter_by(
            date=today_str, metric=metric
        ).first()
        if not row:
            continue
        linked = (
            db.query(models.Habit)
            .filter(
                models.Habit.withings_metric == metric,
                models.Habit.withings_goal.isnot(None),
                models.Habit.archived == False,  # noqa: E712
            )
            .all()
        )
        for habit in linked:
            met = (row.value >= habit.withings_goal) if metric == "steps" else (row.value <= habit.withings_goal)
            if met and not db.query(models.HabitCompletion).filter_by(
                habit_id=habit.id, date=today_str
            ).first():
                db.add(models.HabitCompletion(habit_id=habit.id, date=today_str))
                db.flush()
                recompute_from(db, habit.id, today)


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


def do_sync(db: Session) -> dict:
    """Fetch recent Withings data and upsert into withings_measurements.
    Returns a summary dict."""
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
            return {"ok": False, "error": "invalid_token"}
        except Exception as exc:
            print(f"[withings] token refresh transient error: {exc}", flush=True)
            return {"ok": False, "error": "sync_failed"}

    today = date.today()
    start = today - timedelta(days=89)
    synced = {"steps": 0, "fat_ratio": 0, "weight": 0, "bp_systolic": 0, "bp_diastolic": 0, "heart_rate": 0, "spo2": 0, "sleep_score": 0, "sleep_minutes": 0, "sleep_deep_minutes": 0}
    errors: dict[str, str] = {}

    # ── Steps (activity) ──────────────────────────────────────────────────────
    try:
        body = _withings_get(creds_data, "v2/measure", {
            "action": "getactivity",
            "data_fields": "steps",
            "startdateymd": start.isoformat(),
            "enddateymd": today.isoformat(),
        })
        for item in body.get("activities", []):
            if item.get("steps") is not None:
                _upsert_measurement(db, item["date"], "steps", float(item["steps"]))
                synced["steps"] += 1
    except Exception as exc:
        print(f"[withings] activity sync error: {exc}", flush=True)
        errors["activity"] = str(exc)

    # ── Body measurements (weight, fat, HR, BP, SpO2) ─────────────────────────
    try:
        body = _withings_get(creds_data, "measure", {
            "action": "getmeas",
            "startdate": int(datetime.combine(start, datetime.min.time()).timestamp()),
            "enddate": int(datetime.combine(today + timedelta(days=1), datetime.min.time()).timestamp()),
        })
        seen_types: set[int] = set()
        for group in body.get("measuregrps", []):
            grp_date = date.fromtimestamp(group["date"]).isoformat()
            for measure in group.get("measures", []):
                t = measure.get("type")
                seen_types.add(t)
                raw = measure["value"] * (10 ** measure["unit"])
                if t == 6:    # FAT_RATIO
                    _upsert_measurement(db, grp_date, "fat_ratio", round(raw, 2))
                    synced["fat_ratio"] += 1
                elif t == 1:  # WEIGHT (kg)
                    _upsert_measurement(db, grp_date, "weight", round(raw, 2))
                    synced["weight"] += 1
                elif t == 9:  # DIASTOLIC BP (mmHg)
                    _upsert_measurement(db, grp_date, "bp_diastolic", round(raw, 1))
                    synced["bp_diastolic"] += 1
                elif t == 10: # SYSTOLIC BP (mmHg)
                    _upsert_measurement(db, grp_date, "bp_systolic", round(raw, 1))
                    synced["bp_systolic"] += 1
                elif t == 11: # HEART RATE (bpm)
                    _upsert_measurement(db, grp_date, "heart_rate", round(raw, 1))
                    synced["heart_rate"] += 1
                elif t == 54: # SPO2 (%)
                    _upsert_measurement(db, grp_date, "spo2", round(raw, 1))
                    synced["spo2"] += 1
        # Log which measurement types came back to help diagnose missing data
        print(f"[withings] getmeas returned types: {sorted(seen_types)}", flush=True)
    except Exception as exc:
        print(f"[withings] measurements sync error: {exc}", flush=True)
        errors["measurements"] = str(exc)

    # ── Sleep summary ─────────────────────────────────────────────────────────
    # Requires USER_SLEEP_EVENTS scope; silently skipped if not granted.
    try:
        body = _withings_get(creds_data, "v2/sleep", {
            "action": "getsummary",
            "startdateymd": start.isoformat(),
            "enddateymd": today.isoformat(),
            "data_fields": "sleep_score,total_sleep_time,deep_sleep_duration,spo2_average",
        })
        for item in body.get("series", []):
            d = item.get("date")
            if not d:
                continue
            data = item.get("data", {})
            if data.get("sleep_score") is not None:
                _upsert_measurement(db, d, "sleep_score", float(data["sleep_score"]))
                synced["sleep_score"] += 1
            if data.get("total_sleep_time") is not None:
                _upsert_measurement(db, d, "sleep_minutes", round(float(data["total_sleep_time"]), 0))
                synced["sleep_minutes"] += 1
            if data.get("deep_sleep_duration") is not None:
                _upsert_measurement(db, d, "sleep_deep_minutes", round(float(data["deep_sleep_duration"]), 0))
                synced["sleep_deep_minutes"] += 1
            if data.get("spo2_average") is not None:
                _upsert_measurement(db, d, "spo2", round(float(data["spo2_average"]), 1))
                synced["spo2"] += 1
    except Exception as exc:
        print(f"[withings] sleep sync error: {exc}", flush=True)
        errors["sleep"] = str(exc)

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
    return schemas.WithingsStatus(
        connected=row is not None,
        last_synced=row.last_synced.isoformat() if row and row.last_synced else None,
    )


@router.get("/api/withings/auth-url")
def withings_auth_url():
    """Return the Withings OAuth authorization URL."""
    from withings_api import WithingsAuth, AuthScope
    if not WITHINGS_CLIENT_ID or not WITHINGS_SECRET:
        return JSONResponse({"error": "Withings credentials not configured"}, status_code=503)
    auth = WithingsAuth(
        client_id=WITHINGS_CLIENT_ID,
        consumer_secret=WITHINGS_SECRET,
        callback_uri=WITHINGS_CALLBACK_URI,
        scope=(AuthScope.USER_METRICS, AuthScope.USER_ACTIVITY, AuthScope.USER_SLEEP_EVENTS),
    )
    return {"url": auth.get_authorize_url()}


@router.get("/api/withings/callback")
def withings_callback(code: str, db: Session = Depends(get_db)):
    """OAuth callback: exchange authorization code for tokens."""
    from withings_api.common import Credentials2
    try:
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


import app_setting_keys as setting_keys
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

    # Only fetch completion history for habits that have a withings_metric
    linked_habits = (
        db.query(models.Habit)
        .filter(
            models.Habit.withings_metric.isnot(None),
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
            schemas.WithingsMeasurementOut(date=m.date, metric=m.metric, value=m.value)
            for m in measurements
        ],
        habit_completions=habit_completions,
    )
