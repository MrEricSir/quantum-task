from pydantic import BaseModel, field_validator, computed_field
from typing import List, Literal, Optional
from datetime import datetime, date

from schemas.common import Tag


class CardCreate(BaseModel):
    title: str
    description: Optional[str] = None
    section: str = "today"
    scheduled_at: Optional[datetime] = None
    tag_ids: List[int] = []
    raw_input: Optional[str] = None
    recurrence_rule: Optional[str] = None
    external_id: Optional[str] = None


class CardUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    section: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    completed: Optional[bool] = None
    position: Optional[int] = None
    tag_ids: Optional[List[int]] = None
    recurrence_rule: Optional[str] = None
    archived: Optional[bool] = None
    snoozed_until: Optional[str] = None   # YYYY-MM-DD
    waiting_reason: Optional[str] = None


class ParseRequest(BaseModel):
    text: str


class ParsedCard(BaseModel):
    type: Literal["task", "habit", "goal", "food", "habit_check", "task_complete", "assist", "mood"] = "task"
    title: str
    description: Optional[str] = None
    energy: Optional[int] = None          # 1–5, only for type='mood'
    section: Literal["today", "week", "month", "later"] = "later"
    scheduled_at: Optional[datetime] = None
    suggested_tags: List[str] = []
    recurrence_rule: Optional[Literal["daily", "weekly", "monthly", "yearly"]] = None
    clarification_question: Optional[str] = None
    source_text: Optional[str] = None
    withings_metric: Optional[str] = None
    withings_goal: Optional[float] = None

    @field_validator('scheduled_at', 'description', 'source_text', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if isinstance(v, str) and v.strip().lower() in ('', 'null', 'none'):
            return None
        return v


class BulkParseResponse(BaseModel):
    items: List[ParsedCard]


class CardReorderItem(BaseModel):
    id: int
    section: str
    position: int


class BulkCardItem(BaseModel):
    title: str
    section: str


class BulkCardCreate(BaseModel):
    cards: List[BulkCardItem]


class BreakdownCommit(BaseModel):
    subtasks: List[str]
    tag_name: str


class Card(BaseModel):
    id: int
    title: str
    description: Optional[str]
    body: Optional[str] = None
    section: str
    scheduled_at: Optional[datetime]
    completed: bool
    completed_at: Optional[datetime]
    position: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    archived: bool = False
    archived_at: Optional[datetime] = None
    raw_input: Optional[str] = None
    recurrence_rule: Optional[str] = None
    external_id: Optional[str] = None
    snoozed_until: Optional[str] = None
    waiting_reason: Optional[str] = None
    tags: List[Tag] = []
    thread_output: Optional[str] = None

    @computed_field
    @property
    def overdue_days(self) -> int:
        if self.completed or self.section != 'today' or self.scheduled_at is None:
            return 0
        today = date.today()
        return max(0, (today - self.scheduled_at.date()).days)

    model_config = {"from_attributes": True}
