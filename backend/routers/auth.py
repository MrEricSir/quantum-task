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
import models
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


def _client_ip(request: Request) -> str:
    """Best-effort real client IP for keying per-IP login lockout. Cloud Run terminates
    the connection at Google's front end and forwards to this container over an internal
    connection, so request.client.host reflects that internal hop, not the real caller --
    every request would collapse to the same value, making a per-IP lockout a no-op.
    Google's front end appends the real observed client IP as the LAST entry of
    X-Forwarded-For (any earlier entries could be client-supplied and spoofed) -- taking
    the first entry instead is a common mistake that would let an attacker set their own
    X-Forwarded-For to a fresh fake IP on every request and never actually get locked out.
    Falls back to request.client.host for local dev, where there's no proxy in front and
    that value is already correct."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _get_login_attempt(db: Session, ip: str) -> models.LoginAttempt | None:
    return db.query(models.LoginAttempt).filter_by(ip=ip).first()


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
def auth_login(request: Request, body: _LoginBody, db: Session = Depends(get_db)):
    if not AUTH_PASSWORD:
        return JSONResponse({"ok": True})

    ip = _client_ip(request)
    attempt = _get_login_attempt(db, ip)
    now = datetime.now(timezone.utc)
    if attempt and attempt.lockout_until:
        lockout_until = datetime.fromisoformat(attempt.lockout_until)
        if now < lockout_until:
            retry_minutes = int((lockout_until - now).total_seconds() // 60) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed attempts. Try again in {retry_minutes} minute(s).",
            )

    if not _hmac.compare_digest(body.password, AUTH_PASSWORD):
        if not attempt:
            attempt = models.LoginAttempt(ip=ip, failed_attempts=0, lockout_until=None)
            db.add(attempt)
        attempt.failed_attempts += 1
        if attempt.failed_attempts >= MAX_LOGIN_ATTEMPTS:
            attempt.lockout_until = (now + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
            attempt.failed_attempts = 0
        db.commit()
        raise HTTPException(status_code=401, detail="Wrong password")

    # A successful login means this IP is behaving -- drop its row (if any) rather than
    # just zeroing it out, so the table only ever holds IPs currently mid-lockout or with
    # recent failures, not a permanent record of every IP that's ever logged in.
    if attempt:
        db.delete(attempt)
    s = Settings(db)
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
