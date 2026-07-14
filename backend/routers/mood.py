from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
from deps import get_db, local_date

router = APIRouter()


class MoodLogRequest(BaseModel):
    energy: int           # 1–5
    note: Optional[str] = None


def _row_to_dict(row: models.MoodLog) -> dict:
    return {"date": row.date, "energy": row.energy, "note": row.note}


@router.get("/api/mood/today")
def get_mood_today(request: Request, db: Session = Depends(get_db)):
    today = local_date(request).isoformat()
    row = db.query(models.MoodLog).filter_by(date=today).first()
    return _row_to_dict(row) if row else None


@router.post("/api/mood/log")
def log_mood(request: Request, body: MoodLogRequest, db: Session = Depends(get_db)):
    today = local_date(request).isoformat()
    energy = max(1, min(5, body.energy))
    row = db.query(models.MoodLog).filter_by(date=today).first()
    if row:
        row.energy = energy
        row.note = body.note
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = models.MoodLog(date=today, energy=energy, note=body.note)
        db.add(row)
    db.commit()
    return _row_to_dict(row)


@router.get("/api/mood/recent")
def get_mood_recent(request: Request, days: int = 30, db: Session = Depends(get_db)):
    from datetime import timedelta
    today = local_date(request)
    cutoff = (today - timedelta(days=days)).isoformat()
    rows = (
        db.query(models.MoodLog)
        .filter(models.MoodLog.date >= cutoff)
        .order_by(models.MoodLog.date.desc())
        .all()
    )
    return [_row_to_dict(r) for r in rows]
