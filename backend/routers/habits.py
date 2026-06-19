from datetime import date, datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from deps import get_db, local_date

router = APIRouter()


def _compute_streak(db: Session, habit_id: int, today: date) -> int:
    today_done = db.query(models.HabitCompletion).filter_by(
        habit_id=habit_id, date=today.isoformat()
    ).first() is not None

    streak = 0
    check = today if today_done else today - timedelta(days=1)
    while True:
        done = db.query(models.HabitCompletion).filter_by(
            habit_id=habit_id, date=check.isoformat()
        ).first()
        if done:
            streak += 1
            check -= timedelta(days=1)
        else:
            break
    return streak


def _habit_out(db: Session, habit: models.Habit, today: date) -> schemas.Habit:
    today_str = today.isoformat()
    completed_today = db.query(models.HabitCompletion).filter_by(
        habit_id=habit.id, date=today_str
    ).first() is not None
    recent_completions = [
        db.query(models.HabitCompletion).filter_by(
            habit_id=habit.id, date=(today - timedelta(days=6 - i)).isoformat()
        ).first() is not None
        for i in range(7)
    ]
    return schemas.Habit(
        id=habit.id,
        name=habit.name,
        created_at=habit.created_at,
        tags=list(habit.tags),
        completed_today=completed_today,
        streak=_compute_streak(db, habit.id, today),
        recent_completions=recent_completions,
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
    db_habit = models.Habit(name=habit.name)
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
    db.commit()
    db.refresh(db_habit)
    return _habit_out(db, db_habit, today)


@router.delete("/api/habits/{habit_id}")
def delete_habit(habit_id: int, db: Session = Depends(get_db)):
    db_habit = db.query(models.Habit).filter(models.Habit.id == habit_id).first()
    if not db_habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    db.delete(db_habit)
    db.commit()
    return {"ok": True}


@router.post("/api/habits/{habit_id}/check")
def check_habit(request: Request, habit_id: int, db: Session = Depends(get_db)):
    today_str = local_date(request).isoformat()
    if not db.query(models.HabitCompletion).filter_by(habit_id=habit_id, date=today_str).first():
        db.add(models.HabitCompletion(habit_id=habit_id, date=today_str))
        db.commit()
    return {"ok": True}


@router.delete("/api/habits/{habit_id}/check")
def uncheck_habit(request: Request, habit_id: int, db: Session = Depends(get_db)):
    today_str = local_date(request).isoformat()
    row = db.query(models.HabitCompletion).filter_by(habit_id=habit_id, date=today_str).first()
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}
