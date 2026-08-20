"""
Manual health measurement entry.

Lets a user without Withings hardware (or without a synced reading for a particular metric)
type in a value by hand -- same withings_measurements table Withings sync already writes to
(see migration 00038's `source` column), so charts, habit goal auto-completion, insights, and
Telegram all work identically regardless of where a reading came from.
"""
from datetime import date as _date

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db
from routers.withings import upsert_measurement, auto_check_habits_for_date

router = APIRouter()

# Every metric a manual entry can target -- the full chart-able set (routers.withings.METRICS
# is narrower: just the 3 goal-linkable ones).
MANUAL_METRICS = {
    "steps", "weight", "fat_ratio",
    "bp_systolic", "bp_diastolic", "heart_rate", "spo2",
    "sleep_score", "sleep_minutes", "sleep_deep_minutes",
}


@router.post("/api/health/measurements", response_model=schemas.WithingsMeasurementOut, status_code=201)
def create_health_measurement(payload: schemas.HealthMeasurementCreate, db: Session = Depends(get_db)):
    if payload.metric not in MANUAL_METRICS:
        raise HTTPException(status_code=400, detail=f"Unknown metric: {payload.metric}")
    try:
        parsed_date = _date.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    row = upsert_measurement(db, payload.date, payload.metric, payload.value, source="manual")
    db.commit()

    # A manually-entered steps/weight/fat_ratio value may satisfy a linked habit's goal --
    # give it the same immediate auto-check treatment do_sync() gives synced values, rather
    # than waiting for a Withings sync that may never come for a manual-only user.
    auto_check_habits_for_date(db, parsed_date)
    db.commit()

    return schemas.WithingsMeasurementOut(id=row.id, date=row.date, metric=row.metric, value=row.value, source=row.source)


@router.delete("/api/health/measurements/{measurement_id}")
def delete_health_measurement(measurement_id: int, db: Session = Depends(get_db)):
    row = db.query(models.WithingsMeasurement).filter_by(id=measurement_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Measurement not found")
    db.delete(row)
    db.commit()
    return {"ok": True}
