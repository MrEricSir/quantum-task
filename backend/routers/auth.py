import hmac as _hmac

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from deps import AUTH_PASSWORD, SESSION_TOKEN

router = APIRouter()


class _LoginBody(BaseModel):
    password: str


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/api/auth/check")
def auth_check(request: Request):
    if not AUTH_PASSWORD:
        return {"authed": True, "enabled": False}
    token = request.cookies.get("session", "")
    return {"authed": _hmac.compare_digest(token, SESSION_TOKEN), "enabled": True}


@router.post("/api/auth/login")
def auth_login(body: _LoginBody):
    if not AUTH_PASSWORD:
        return JSONResponse({"ok": True})
    if not _hmac.compare_digest(body.password, AUTH_PASSWORD):
        raise HTTPException(status_code=401, detail="Wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "session", SESSION_TOKEN,
        httponly=True, samesite="lax", max_age=30 * 24 * 3600,
    )
    return resp


@router.post("/api/auth/logout")
def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session", samesite="lax")
    return resp
