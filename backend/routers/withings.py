"""
Withings OAuth + data sync router.

OAuth flow:
  1. GET /api/withings/auth-url  → frontend opens returned URL in new tab
  2. User authorises in Withings; Withings redirects to WITHINGS_CALLBACK_URI
  3. GET /api/withings/callback  → exchanges code for tokens, stores credentials,
                                    redirects browser to {ALLOWED_ORIGIN}/health
  4. POST /api/withings/sync     → manual or scheduled sync
  5. GET /api/withings/health-data → measurements + per-habit completion history

Credentials are stored as JSON in the app_settings table under key
"withings_credentials". Last-sync timestamp is stored under
"withings_last_synced".
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

router = APIRouter()

WITHINGS_CLIENT_ID = os.getenv("WITHINGS_CLIENT_ID", "")
WITHINGS_SECRET = os.getenv("WITHINGS_SECRET", "")
WITHINGS_CALLBACK_URI = os.getenv(
    "WITHINGS_CALLBACK_URI", "http://localhost:8000/api/withings/callback"
)
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:5173")

METRICS = {"steps", "fat_ratio"}


# ── Credential helpers ────────────────────────────────────────────────────────

def _save_credentials(db: Session, creds) -> None:
    """Persist Credentials2 object to app_settings."""
    payload = json.dumps({
        "access_token": creds.access_token,
        "token_type": creds.token_type,
        "refresh_token": creds.refresh_token,
        "userid": creds.userid,
        "client_id": creds.client_id,
        "consumer_secret": creds.consumer_secret,
        "expires_in": creds.expires_in,
    })
    db.merge(models.AppSetting(key="withings_credentials", value=payload))
    db.commit()


def _load_credentials(db: Session):
    """Load stored Credentials2, or None if not connected."""
    from withings_api.common import Credentials2
    row = db.query(models.AppSetting).filter_by(key="withings_credentials").first()
    if not row:
        return None
    try:
        data = json.loads(row.value)
        return Credentials2(
            access_token=data["access_token"],
            token_type=data["token_type"],
            refresh_token=data["refresh_token"],
            userid=data["userid"],
            client_id=data["client_id"],
            consumer_secret=data["consumer_secret"],
            expires_in=data["expires_in"],
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


def _auto_check_step_habits(db: Session, today: date) -> None:
    """Auto-complete any step-linked habits whose goal was met today."""
    today_str = today.isoformat()
    row = db.query(models.WithingsMeasurement).filter_by(
        date=today_str, metric="steps"
    ).first()
    if not row:
        return

    step_habits = (
        db.query(models.Habit)
        .filter(
            models.Habit.withings_metric == "steps",
            models.Habit.withings_goal.isnot(None),
            models.Habit.archived == False,  # noqa: E712
        )
        .all()
    )
    for habit in step_habits:
        if row.value >= habit.withings_goal:
            if not db.query(models.HabitCompletion).filter_by(
                habit_id=habit.id, date=today_str
            ).first():
                db.add(models.HabitCompletion(habit_id=habit.id, date=today_str))


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


def do_sync(db: Session) -> dict:
    """Fetch recent Withings data and upsert into withings_measurements.
    Returns a summary dict."""
    row = db.query(models.AppSetting).filter_by(key="withings_credentials").first()
    if not row:
        return {"ok": False, "error": "not_connected"}

    try:
        creds_data = json.loads(row.value)
    except Exception:
        return {"ok": False, "error": "invalid_credentials"}

    today = date.today()
    start = today - timedelta(days=89)
    synced = {"steps": 0, "fat_ratio": 0}

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

    # ── Body fat % (measurements) ─────────────────────────────────────────────
    try:
        body = _withings_get(creds_data, "measure", {
            "action": "getmeas",
            "meastype": 6,  # FAT_RATIO
            "startdate": int(datetime.combine(start, datetime.min.time()).timestamp()),
            "enddate": int(datetime.combine(today, datetime.min.time()).timestamp()),
        })
        for group in body.get("measuregrps", []):
            grp_date = date.fromtimestamp(group["date"]).isoformat()
            for measure in group.get("measures", []):
                if measure.get("type") == 6:  # FAT_RATIO
                    value = measure["value"] * (10 ** measure["unit"])
                    _upsert_measurement(db, grp_date, "fat_ratio", round(value, 2))
                    synced["fat_ratio"] += 1
    except Exception as exc:
        print(f"[withings] measurements sync error: {exc}", flush=True)

    db.commit()
    _auto_check_step_habits(db, today)
    db.commit()

    db.merge(models.AppSetting(
        key="withings_last_synced",
        value=datetime.now(timezone.utc).isoformat(),
    ))
    db.commit()

    return {"ok": True, "synced": synced}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/withings/status", response_model=schemas.WithingsStatus)
def withings_status(db: Session = Depends(get_db)):
    creds = _load_credentials(db)
    last_row = db.query(models.AppSetting).filter_by(key="withings_last_synced").first()
    return schemas.WithingsStatus(
        connected=creds is not None,
        last_synced=last_row.value if last_row else None,
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
        scope=(AuthScope.USER_METRICS, AuthScope.USER_ACTIVITY),
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
        try:
            do_sync(db)
        except Exception:
            traceback.print_exc()
    except Exception as exc:
        traceback.print_exc()
        print(f"[withings] callback error: {exc}")
        return RedirectResponse(f"{ALLOWED_ORIGIN}/health?withings=error&msg={exc}")
    return RedirectResponse(f"{ALLOWED_ORIGIN}/health?withings=connected")


@router.post("/api/withings/sync")
def withings_sync(db: Session = Depends(get_db)):
    """Manually trigger a Withings data sync."""
    return do_sync(db)


@router.delete("/api/withings/disconnect")
def withings_disconnect(db: Session = Depends(get_db)):
    """Remove stored Withings credentials."""
    for key in ("withings_credentials", "withings_last_synced"):
        row = db.query(models.AppSetting).filter_by(key=key).first()
        if row:
            db.delete(row)
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
