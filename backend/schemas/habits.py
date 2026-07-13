from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from schemas.common import Tag


class HabitCreate(BaseModel):
    name: str
    tag_ids: List[int] = []
    withings_metric: Optional[str] = None
    withings_goal: Optional[float] = None


class HabitUpdate(BaseModel):
    name: Optional[str] = None
    tag_ids: Optional[List[int]] = None
    archived: Optional[bool] = None
    withings_metric: Optional[str] = None
    withings_goal: Optional[float] = None


class Habit(BaseModel):
    id: int
    name: str
    created_at: datetime
    archived: bool = False
    archived_at: Optional[datetime] = None
    tags: List[Tag] = []
    completed_today: bool = False
    streak: int = 0
    recent_completions: List[bool] = []
    withings_metric: Optional[str] = None
    withings_goal: Optional[float] = None
    is_experiment: bool = False

    model_config = {"from_attributes": True}


class HabitStreakDayOut(BaseModel):
    date: str
    streak: int


class HabitBriefingItem(BaseModel):
    name: str
    completed_today: bool
