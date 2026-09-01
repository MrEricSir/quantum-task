from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db, local_date, utc_offset_minutes
from settings import Settings
from streak import recompute_all_habits
from telegram.notify import send_message
from trip.generate import generate_trip_retrospective

router = APIRouter()

# Below this, ending a trip is treated as an accidental toggle rather than a real trip --
# no retrospective generated or sent. Measured against Trip.created_at (a real timestamp),
# not start_date (a bare YYYY-MM-DD), so this works correctly regardless of what day it is.
MIN_TRIP_DURATION_MINUTES = 60


@router.get("/api/trip", response_model=schemas.Trip | None)
def get_trip(db: Session = Depends(get_db)):
    """The active trip (end_date is null) if any, else the most recently ended one."""
    active = db.query(models.Trip).filter(models.Trip.end_date.is_(None)).first()
    if active:
        return active
    return db.query(models.Trip).order_by(models.Trip.id.desc()).first()


@router.post("/api/trip", response_model=schemas.Trip)
def start_trip(request: Request, body: schemas.TripCreate, db: Session = Depends(get_db)):
    if db.query(models.Trip).filter(models.Trip.end_date.is_(None)).first():
        raise HTTPException(400, "A trip is already active -- end it before starting a new one.")
    today = local_date(request)
    trip = models.Trip(
        name=(body.name or "").strip() or None,
        start_date=body.start_date or today.isoformat(),
    )
    db.add(trip)
    db.commit()
    db.refresh(trip)
    recompute_all_habits(db, today)
    db.commit()
    return trip


@router.put("/api/trip/{trip_id}", response_model=schemas.Trip)
def update_trip(request: Request, trip_id: int, body: schemas.TripUpdate, db: Session = Depends(get_db)):
    trip = db.query(models.Trip).filter(models.Trip.id == trip_id).first()
    if not trip:
        raise HTTPException(404, "Trip not found")
    if body.name is not None:
        trip.name = body.name.strip() or None
    if body.start_date is not None:
        trip.start_date = body.start_date
    db.commit()
    recompute_all_habits(db, local_date(request))
    db.commit()
    db.refresh(trip)
    return trip


@router.post("/api/trip/{trip_id}/end", response_model=schemas.TripEndResult)
def end_trip(request: Request, trip_id: int, db: Session = Depends(get_db)):
    trip = db.query(models.Trip).filter(models.Trip.id == trip_id).first()
    if not trip:
        raise HTTPException(404, "Trip not found")
    if trip.end_date is not None:
        raise HTTPException(400, "Trip already ended")

    today = local_date(request)
    trip.end_date = today.isoformat()

    elapsed = datetime.now(timezone.utc).replace(tzinfo=None) - trip.created_at
    too_short = elapsed < timedelta(minutes=MIN_TRIP_DURATION_MINUTES)
    if too_short:
        trip.retrospective_skipped = True
    db.commit()
    recompute_all_habits(db, today)
    db.commit()

    # Best-effort immediate send -- telegram/scheduler.py's check_trip_retrospective is a
    # backstop that retries on the next tick if this fails or Telegram isn't configured.
    retrospective = None
    if not too_short:
        s = Settings(db)
        token, chat_id = s.telegram_token, s.telegram_chat_id
        if token and chat_id:
            tz_offset = utc_offset_minutes(request)
            retrospective = generate_trip_retrospective(trip.start_date, trip.end_date, trip.name, tz_offset)
            if retrospective and send_message(token, chat_id, retrospective):
                trip.retrospective_sent = True
                db.commit()

    db.refresh(trip)
    return schemas.TripEndResult(trip=trip, retrospective=retrospective)


@router.delete("/api/trip/{trip_id}")
def delete_trip(request: Request, trip_id: int, db: Session = Depends(get_db)):
    trip = db.query(models.Trip).filter(models.Trip.id == trip_id).first()
    if not trip:
        raise HTTPException(404, "Trip not found")
    db.delete(trip)
    db.commit()
    recompute_all_habits(db, local_date(request))
    db.commit()
    return {"ok": True}
