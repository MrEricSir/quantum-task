from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db, local_date
from streak import recompute_from, get_current_streak

router = APIRouter()


def _habit_out(db: Session, habit: models.Habit, today: date) -> schemas.Habit:
    today_str = today.isoformat()
    week_start = (today - timedelta(days=6)).isoformat()

    # Single query for the last 7 days of streak data (completed days only).
    week_entries = {
        e.date
        for e in db.query(models.HabitStreakDay).filter(
            models.HabitStreakDay.habit_id == habit.id,
            models.HabitStreakDay.date >= week_start,
            models.HabitStreakDay.date <= today_str,
        ).all()
    }
    recent_completions = [
        (today - timedelta(days=6 - i)).isoformat() in week_entries
        for i in range(7)
    ]

    is_experiment = (
        db.query(models.HealthExperiment)
        .filter_by(habit_id=habit.id, status="active")
        .first()
    ) is not None

    return schemas.Habit(
        id=habit.id,
        name=habit.name,
        created_at=habit.created_at,
        tags=list(habit.tags),
        completed_today=today_str in week_entries,
        streak=get_current_streak(db, habit.id, today),
        recent_completions=recent_completions,
        withings_metric=habit.withings_metric,
        withings_goal=habit.withings_goal,
        is_experiment=is_experiment,
    )


@router.get("/api/habits", response_model=List[schemas.Habit])
def get_habits(request: Request, archived: bool = False, db: Session = Depends(get_db)):
    today = local_date(request)
    habits = (
        db.query(models.Habit)
        .filter(models.Habit.archived == archived)
        .order_by(models.Habit.created_at)
        .all()
    )
    return [_habit_out(db, h, today) for h in habits]


@router.post("/api/habits", response_model=schemas.Habit, status_code=201)
def create_habit(request: Request, habit: schemas.HabitCreate, db: Session = Depends(get_db)):
    today = local_date(request)
    db_habit = models.Habit(
        name=habit.name,
        withings_metric=habit.withings_metric,
        withings_goal=habit.withings_goal,
    )
    if habit.tag_ids:
        db_habit.tags = db.query(models.Tag).filter(models.Tag.id.in_(habit.tag_ids)).all()
    db.add(db_habit)
    db.commit()
    db.refresh(db_habit)
    return _habit_out(db, db_habit, today)


@router.put("/api/habits/{habit_id}", response_model=schemas.Habit)
def update_habit(request: Request, habit_id: int, habit: schemas.HabitUpdate, db: Session = Depends(get_db)):
    today = local_date(request)
    db_habit = db.query(models.Habit).filter(models.Habit.id == habit_id).first()
    if not db_habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    if habit.name is not None:
        db_habit.name = habit.name
    if habit.tag_ids is not None:
        db_habit.tags = db.query(models.Tag).filter(models.Tag.id.in_(habit.tag_ids)).all()
    if habit.archived is not None:
        db_habit.archived = habit.archived
        db_habit.archived_at = datetime.now(timezone.utc) if habit.archived else None
    if "withings_metric" in habit.model_fields_set:
        db_habit.withings_metric = habit.withings_metric
    if "withings_goal" in habit.model_fields_set:
        db_habit.withings_goal = habit.withings_goal
    db.commit()
    db.refresh(db_habit)
    return _habit_out(db, db_habit, today)


@router.get("/api/habits/{habit_id}/streak-days", response_model=List[schemas.HabitStreakDayOut])
def get_streak_days(
    habit_id: int,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    query = db.query(models.HabitStreakDay).filter(
        models.HabitStreakDay.habit_id == habit_id
    )
    if from_date:
        query = query.filter(models.HabitStreakDay.date >= from_date)
    if to_date:
        query = query.filter(models.HabitStreakDay.date <= to_date)
    rows = query.order_by(models.HabitStreakDay.date).all()
    return [schemas.HabitStreakDayOut(date=r.date, streak=r.streak) for r in rows]


@router.delete("/api/habits/{habit_id}")
def delete_habit(habit_id: int, db: Session = Depends(get_db)):
    db_habit = db.query(models.Habit).filter(models.Habit.id == habit_id).first()
    if not db_habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    db.delete(db_habit)
    db.commit()
    return {"ok": True}


def _require_manual(habit_id: int, db: Session) -> models.Habit:
    """Return the habit or raise if it is auto-tracked via Withings."""
    db_habit = db.query(models.Habit).filter(models.Habit.id == habit_id).first()
    if not db_habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    if db_habit.withings_metric:
        raise HTTPException(
            status_code=403,
            detail="This habit is tracked automatically and cannot be checked manually.",
        )
    return db_habit


@router.post("/api/habits/{habit_id}/check")
def check_habit(request: Request, habit_id: int, db: Session = Depends(get_db)):
    _require_manual(habit_id, db)
    today = local_date(request)
    today_str = today.isoformat()
    if not db.query(models.HabitCompletion).filter_by(habit_id=habit_id, date=today_str).first():
        db.add(models.HabitCompletion(habit_id=habit_id, date=today_str))
        db.flush()
        recompute_from(db, habit_id, today)
        db.commit()
    return {"ok": True}


@router.delete("/api/habits/{habit_id}/check")
def uncheck_habit(request: Request, habit_id: int, db: Session = Depends(get_db)):
    _require_manual(habit_id, db)
    today = local_date(request)
    today_str = today.isoformat()
    row = db.query(models.HabitCompletion).filter_by(habit_id=habit_id, date=today_str).first()
    if row:
        db.delete(row)
        db.flush()
        recompute_from(db, habit_id, today)
        db.commit()
    return {"ok": True}
