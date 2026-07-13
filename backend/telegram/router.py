"""
Telegram FastAPI endpoints — thin HTTP adapters only.

Business logic lives in telegram.bot and telegram.scheduler.
"""
import hmac
import secrets

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import app_setting_keys as keys
import models
from deps import get_db
from settings import Settings
from telegram.bot import handle_update
from telegram.notify import send_message, set_webhook, get_webhook_info
from briefing import generate_today_briefing
from datetime import datetime, timezone, timedelta

router = APIRouter()


# ── Config ────────────────────────────────────────────────────────────────────

class _TelegramConfig(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    schedule_time: str = "07:30"
    tz_offset: int = 0
    habit_reminder_time: str = ""
    overdue_nudge_time: str = ""


@router.get("/api/telegram/config")
def get_telegram_config(db: Session = Depends(get_db)):
    s = Settings(db)
    return {
        "bot_token":           s.telegram_token,
        "chat_id":             s.telegram_chat_id,
        "schedule_time":       s.briefing_schedule_time,
        "tz_offset":           s.tz_offset,
        "habit_reminder_time": s.habit_reminder_time,
        "overdue_nudge_time":  s.overdue_nudge_time,
    }


@router.put("/api/telegram/config")
def save_telegram_config(body: _TelegramConfig, db: Session = Depends(get_db)):
    s = Settings(db)
    s.set(keys.TELEGRAM_BOT_TOKEN,     body.bot_token.strip())
    s.set(keys.TELEGRAM_CHAT_ID,       body.chat_id.strip())
    s.set(keys.BRIEFING_SCHEDULE_TIME, body.schedule_time.strip() or "07:30")
    s.set(keys.BRIEFING_TZ_OFFSET,     str(body.tz_offset))
    s.set(keys.HABIT_REMINDER_TIME,    body.habit_reminder_time.strip())
    s.set(keys.OVERDUE_NUDGE_TIME,     body.overdue_nudge_time.strip())
    db.commit()
    return {"ok": True}


# ── Scheduled briefing (called by Cloud Scheduler) ────────────────────────────

@router.post("/api/telegram/daily-briefing")
def daily_briefing(db: Session = Depends(get_db)):
    """Called by Cloud Scheduler hourly. Runs all scheduled notification checks."""
    from telegram.scheduler import check_all
    results = check_all(db)
    return {"ok": True, "results": results}


# ── Test message ──────────────────────────────────────────────────────────────

@router.post("/api/telegram/test")
def test_telegram(db: Session = Depends(get_db)):
    """Send the today briefing immediately as a test."""
    s = Settings(db)
    token   = s.telegram_token
    chat_id = s.telegram_chat_id

    if not token or not chat_id:
        return {"ok": False, "error": "Bot token and chat ID must be configured first."}

    tz_offset = s.tz_offset
    today = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)).date()
    try:
        text = generate_today_briefing(today, tz_offset)
    except Exception as e:
        return {"ok": False, "error": f"Briefing generation error: {e}"}

    if not text:
        return {"ok": False, "error": "Could not generate briefing (LLM error)."}

    ok = send_message(token, chat_id, text)
    if not ok:
        return {"ok": False, "error": "Message failed. Check that the bot token and chat ID are correct."}
    return {"ok": True}


# ── Webhook registration ──────────────────────────────────────────────────────

@router.post("/api/telegram/register-webhook")
def register_webhook(request: Request, db: Session = Depends(get_db)):
    s = Settings(db)
    token = s.telegram_token
    if not token:
        return {"ok": False, "error": "Bot token not configured."}

    secret = s.telegram_webhook_secret
    if not secret:
        secret = secrets.token_hex(32)
        s.set(keys.TELEGRAM_WEBHOOK_SECRET, secret)
        db.commit()

    base = str(request.base_url).rstrip("/").replace("http://", "https://", 1)
    webhook_url = f"{base}/api/telegram/webhook"

    try:
        result = set_webhook(token, webhook_url, secret)
    except Exception as e:
        return {"ok": False, "error": f"Telegram API error: {e}"}

    if not result.get("ok"):
        return {"ok": False, "error": result.get("description", "Unknown error")}

    return {"ok": True, "webhook_url": webhook_url}


# ── Incoming webhook ──────────────────────────────────────────────────────────

@router.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Receives incoming Telegram updates. Processed synchronously — Cloud Run
    throttles CPU after the response is sent so BackgroundTasks won't run."""
    s = Settings(db)
    secret = s.telegram_webhook_secret
    if secret and not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
        return {"ok": False}

    body = await request.json()
    handle_update(body)
    return {"ok": True}


# ── Diagnostics ───────────────────────────────────────────────────────────────

@router.get("/api/telegram/webhook-info")
def webhook_info(db: Session = Depends(get_db)):
    s = Settings(db)
    token = s.telegram_token
    if not token:
        return {"ok": False, "error": "Bot token not configured."}
    try:
        return get_webhook_info(token)
    except Exception as e:
        return {"ok": False, "error": str(e)}


class _SimulateMessage(BaseModel):
    text: str
    chat_id: str = ""


@router.post("/api/telegram/simulate-message")
def simulate_message(body: _SimulateMessage, db: Session = Depends(get_db)):
    """Directly invoke the message handler — useful for local testing."""
    s = Settings(db)
    chat_id = body.chat_id or s.telegram_chat_id
    fake_update = {
        "message": {
            "text": body.text,
            "chat": {"id": int(chat_id) if chat_id.lstrip("-").isdigit() else 0},
        }
    }
    handle_update(fake_update)
    return {"ok": True, "routed": body.text}
