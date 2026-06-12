from pydantic import BaseModel, field_validator, computed_field
from typing import List, Literal, Optional
from datetime import datetime, date


class Tag(BaseModel):
    id: int
    name: str
    color: str

    model_config = {"from_attributes": True}


class TagCreate(BaseModel):
    name: str
    color: str = "#6b7280"


class TagUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None


class TagReplacement(BaseModel):
    new_tag_id: int


class TodoCreate(BaseModel):
    title: str
    # description: optional context — for reference cards (section="none") this is the full text content
    description: Optional[str] = None
    body: Optional[str] = None  # legacy field — ignored in new code
    section: str = "today"
    scheduled_at: Optional[datetime] = None
    tag_ids: List[int] = []
    raw_input: Optional[str] = None
    recurrence_rule: Optional[str] = None
    external_id: Optional[str] = None


class TodoUpdate(BaseModel):
    title: Optional[str] = None
    # description: see TodoCreate — same semantics
    description: Optional[str] = None
    body: Optional[str] = None  # legacy field — ignored in new code
    section: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    completed: Optional[bool] = None
    position: Optional[int] = None
    tag_ids: Optional[List[int]] = None
    recurrence_rule: Optional[str] = None
    archived: Optional[bool] = None


class ParseRequest(BaseModel):
    text: str


class ParsedTodo(BaseModel):
    # type: "task" = completable item; "habit" = ongoing recurring behaviour
    type: Literal["task", "habit"] = "task"
    title: str
    # description: optional short context from the user's input
    #   board tasks: shown in the detail modal as extra context
    #   reference cards (section="none"): serves as the card's text content
    description: Optional[str] = None
    # section: "none" = reference card (Cards page only), otherwise board column
    section: Literal["today", "week", "month", "later", "none"] = "later"
    scheduled_at: Optional[datetime] = None
    suggested_tags: List[str] = []
    recurrence_rule: Optional[Literal["daily", "weekly", "monthly", "yearly"]] = None
    clarification_question: Optional[str] = None

    @field_validator('scheduled_at', 'description', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        return None if v == '' else v


class BulkParseResponse(BaseModel):
    items: List[ParsedTodo]


class HabitCreate(BaseModel):
    name: str
    tag_ids: List[int] = []


class HabitUpdate(BaseModel):
    name: Optional[str] = None
    tag_ids: Optional[List[int]] = None
    archived: Optional[bool] = None


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

    model_config = {"from_attributes": True}


class HabitBriefingItem(BaseModel):
    name: str
    completed_today: bool


class CalendarMappingItem(BaseModel):
    id: Optional[int] = None
    tag_id: int
    ical_url: str
    name: str = ""


class CalendarEvent(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    start: datetime
    end: Optional[datetime] = None
    all_day: bool = False
    section: str
    tag_id: Optional[int] = None
    tag_name: Optional[str] = None
    tag_color: Optional[str] = None
    feed_name: Optional[str] = None


class NoteCreate(BaseModel):
    title: Optional[str] = None
    content: str = ""
    tag_ids: List[int] = []


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tag_ids: Optional[List[int]] = None
    archived: Optional[bool] = None


class Note(BaseModel):
    id: int
    title: Optional[str]
    content: str
    created_at: datetime
    updated_at: datetime
    archived: bool = False
    archived_at: Optional[datetime] = None
    tags: List[Tag] = []

    model_config = {"from_attributes": True}


class BriefingRequest(BaseModel):
    todos: List['Todo'] = []
    calendar_events: List[CalendarEvent] = []
    habits: List[HabitBriefingItem] = []
    lat: Optional[float] = None
    lon: Optional[float] = None
    force: bool = False
    today_only: bool = False
    utc_offset_minutes: Optional[int] = None


class EngineeringItem(BaseModel):
    id: int
    external_id: str
    title: str
    item_type: str
    repo: str
    number: int
    url: str
    state: str
    synced_at: datetime

    model_config = {"from_attributes": True}


class TodoReorderItem(BaseModel):
    id: int
    section: str
    position: int


class Todo(BaseModel):
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
    tags: List[Tag] = []

    @computed_field
    @property
    def overdue_days(self) -> int:
        if self.completed or self.section != 'today':
            return 0
        today = date.today()
        ref = (self.scheduled_at or self.created_at).date()
        return max(0, (today - ref).days)

    model_config = {"from_attributes": True}
