from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

import app_setting_keys as setting_keys
import models
import telegram_notify
from deps import get_db
from routers.briefing import generate_today_briefing

router = APIRouter()


class _TelegramConfig(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    schedule_time: str = "07:30"
    tz_offset: int = 0  # JS convention: UTC+10 → -600, UTC-5 → +300


def _get(db: Session, key: str, default: str = "") -> str:
    row = db.query(models.AppSetting).filter_by(key=key).first()
    return row.value if row and row.value else default


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(models.AppSetting).filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.add(models.AppSetting(key=key, value=value))


@router.get("/api/telegram/config")
def get_telegram_config(db: Session = Depends(get_db)):
    return {
        "bot_token":     _get(db, setting_keys.TELEGRAM_BOT_TOKEN),
        "chat_id":       _get(db, setting_keys.TELEGRAM_CHAT_ID),
        "schedule_time": _get(db, setting_keys.BRIEFING_SCHEDULE_TIME, "07:30"),
        "tz_offset":     int(_get(db, setting_keys.BRIEFING_TZ_OFFSET, "0") or "0"),
    }


@router.put("/api/telegram/config")
def save_telegram_config(body: _TelegramConfig, db: Session = Depends(get_db)):
    _set(db, setting_keys.TELEGRAM_BOT_TOKEN,     body.bot_token.strip())
    _set(db, setting_keys.TELEGRAM_CHAT_ID,       body.chat_id.strip())
    _set(db, setting_keys.BRIEFING_SCHEDULE_TIME, body.schedule_time.strip() or "07:30")
    _set(db, setting_keys.BRIEFING_TZ_OFFSET,     str(body.tz_offset))
    db.commit()
    return {"ok": True}


@router.post("/api/telegram/daily-briefing")
def daily_briefing(db: Session = Depends(get_db)):
    """Called by Cloud Scheduler hourly. Sends the briefing during the configured hour."""
    token   = _get(db, setting_keys.TELEGRAM_BOT_TOKEN)
    chat_id = _get(db, setting_keys.TELEGRAM_CHAT_ID)
    if not token or not chat_id:
        return {"ok": False, "skipped": True, "reason": "not configured"}

    schedule_time = _get(db, setting_keys.BRIEFING_SCHEDULE_TIME, "07:30")
    tz_offset     = int(_get(db, setting_keys.BRIEFING_TZ_OFFSET, "0") or "0")

    now_local = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset))
    today     = now_local.date()

    try:
        sh = int(schedule_time.split(":")[0])
    except Exception:
        sh = 7

    if now_local.hour != sh:
        return {"ok": False, "skipped": True, "reason": "outside send window"}

    # DB-backed dedup — prevents double-send on concurrent invocations / cold starts
    last_sent = _get(db, setting_keys.BRIEFING_LAST_SENT)
    if last_sent == today.isoformat():
        return {"ok": False, "skipped": True, "reason": "already sent today"}

    row = db.query(models.AppSetting).filter_by(key=setting_keys.BRIEFING_LAST_SENT).first()
    if row:
        row.value = today.isoformat()
    else:
        db.add(models.AppSetting(key=setting_keys.BRIEFING_LAST_SENT, value=today.isoformat()))
    db.commit()

    try:
        text = generate_today_briefing(today, tz_offset)
    except Exception as e:
        return {"ok": False, "error": f"Briefing generation error: {e}"}

    if not text:
        return {"ok": False, "error": "Could not generate briefing (LLM error)."}

    ok = telegram_notify.send_message(token, chat_id, text)
    if not ok:
        return {"ok": False, "error": "Message failed. Check bot token and chat ID."}
    return {"ok": True}


@router.post("/api/telegram/test")
def test_telegram(db: Session = Depends(get_db)):
    """Send the today briefing immediately as a test."""
    token   = _get(db, setting_keys.TELEGRAM_BOT_TOKEN)
    chat_id = _get(db, setting_keys.TELEGRAM_CHAT_ID)
    tz_offset = int(_get(db, setting_keys.BRIEFING_TZ_OFFSET, "0") or "0")

    if not token or not chat_id:
        return {"ok": False, "error": "Bot token and chat ID must be configured first."}

    today = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)).date()
    try:
        text = generate_today_briefing(today, tz_offset)
    except Exception as e:
        return {"ok": False, "error": f"Briefing generation error: {e}"}

    if not text:
        return {"ok": False, "error": "Could not generate briefing (LLM error)."}

    ok = telegram_notify.send_message(token, chat_id, text)
    if not ok:
        return {"ok": False, "error": "Message failed. Check that the bot token and chat ID are correct."}
    return {"ok": True}
