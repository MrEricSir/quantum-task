import hmac as _hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import app_setting_keys as keys
from deps import AUTH_PASSWORD, get_db
from settings import Settings

router = APIRouter()

# https:// in production (real ALLOWED_ORIGIN); local dev's http://localhost default
# leaves this off since browsers won't send a Secure cookie back over plain http.
_COOKIE_SECURE = os.getenv("ALLOWED_ORIGIN", "http://localhost:5173").startswith("https://")

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


class _LoginBody(BaseModel):
    password: str


def _get_or_create_session_secret(s: Settings) -> str:
    secret = s.session_secret
    if secret:
        return secret
    secret = secrets.token_hex(32)
    s.set(keys.SESSION_SECRET, secret)
    return secret


def _rotate_session_secret(s: Settings) -> str:
    """Generate a fresh session secret, invalidating every outstanding session
    cookie (not just the caller's) since they all compare against this one value."""
    secret = secrets.token_hex(32)
    s.set(keys.SESSION_SECRET, secret)
    return secret


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/api/auth/check")
def auth_check(request: Request, db: Session = Depends(get_db)):
    if not AUTH_PASSWORD:
        return {"authed": True, "enabled": False}
    s = Settings(db)
    session_secret = _get_or_create_session_secret(s)
    db.commit()
    token = request.cookies.get("session", "")
    return {"authed": _hmac.compare_digest(token, session_secret), "enabled": True}


@router.post("/api/auth/login")
def auth_login(body: _LoginBody, db: Session = Depends(get_db)):
    if not AUTH_PASSWORD:
        return JSONResponse({"ok": True})

    s = Settings(db)
    now = datetime.now(timezone.utc)
    lockout_until_raw = s.auth_lockout_until
    if lockout_until_raw:
        lockout_until = datetime.fromisoformat(lockout_until_raw)
        if now < lockout_until:
            retry_minutes = int((lockout_until - now).total_seconds() // 60) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed attempts. Try again in {retry_minutes} minute(s).",
            )

    if not _hmac.compare_digest(body.password, AUTH_PASSWORD):
        attempts = s.auth_failed_attempts + 1
        if attempts >= MAX_LOGIN_ATTEMPTS:
            s.set(keys.AUTH_LOCKOUT_UNTIL, (now + timedelta(minutes=LOCKOUT_MINUTES)).isoformat())
            s.set(keys.AUTH_FAILED_ATTEMPTS, "0")
        else:
            s.set(keys.AUTH_FAILED_ATTEMPTS, str(attempts))
        db.commit()
        raise HTTPException(status_code=401, detail="Wrong password")

    s.set(keys.AUTH_FAILED_ATTEMPTS, "0")
    s.set(keys.AUTH_LOCKOUT_UNTIL, "")
    session_secret = _get_or_create_session_secret(s)
    db.commit()

    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "session", session_secret,
        httponly=True, samesite="lax", secure=_COOKIE_SECURE, max_age=30 * 24 * 3600,
    )
    return resp


@router.post("/api/auth/logout")
def auth_logout(db: Session = Depends(get_db)):
    """Rotates the session secret (see _rotate_session_secret) so this actually
    revokes access rather than only clearing the calling browser's own cookie."""
    if AUTH_PASSWORD:
        s = Settings(db)
        _rotate_session_secret(s)
        db.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session", samesite="lax")
    return resp
