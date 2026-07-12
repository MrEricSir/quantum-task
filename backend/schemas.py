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


class CardCreate(BaseModel):
    title: str
    # description: optional context or extra detail from the user's input
    description: Optional[str] = None
    section: str = "today"
    scheduled_at: Optional[datetime] = None
    tag_ids: List[int] = []
    raw_input: Optional[str] = None
    recurrence_rule: Optional[str] = None
    external_id: Optional[str] = None


class CardUpdate(BaseModel):
    title: Optional[str] = None
    # description: see CardCreate — same semantics
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
    # type: "task" = completable item; "habit" = ongoing recurring behaviour
    #       "goal" = update a standalone health goal (not a habit or task)
    #       "food" = log something eaten or drunk
    #       "habit_check" = mark an existing habit as done today
    type: Literal["task", "habit", "goal", "food", "habit_check", "task_complete", "assist"] = "task"
    title: str
    # description: optional short context from the user's input
    description: Optional[str] = None
    # section: board column for the card
    section: Literal["today", "week", "month", "later"] = "later"
    scheduled_at: Optional[datetime] = None
    suggested_tags: List[str] = []
    recurrence_rule: Optional[Literal["daily", "weekly", "monthly", "yearly"]] = None
    clarification_question: Optional[str] = None

    source_text: Optional[str] = None  # verbatim fragment from the original input

    # Only populated when type="habit" and the input mentions a Withings health metric
    withings_metric: Optional[str] = None   # 'steps' | 'fat_ratio' | 'weight'
    withings_goal: Optional[float] = None   # numeric goal; steps ≥ goal, others ≤ goal

    @field_validator('scheduled_at', 'description', 'source_text', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if isinstance(v, str) and v.strip().lower() in ('', 'null', 'none'):
            return None
        return v


class BulkParseResponse(BaseModel):
    items: List[ParsedCard]


class HabitCreate(BaseModel):
    name: str
    tag_ids: List[int] = []
    withings_metric: Optional[str] = None   # 'steps' | 'fat_ratio'
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
    is_experiment: bool = False  # linked to an active HealthExperiment

    model_config = {"from_attributes": True}


class WithingsStatus(BaseModel):
    connected: bool
    last_synced: Optional[str] = None  # ISO datetime string


class WithingsMeasurementOut(BaseModel):
    date: str
    metric: str
    value: float


class WithingsHealthData(BaseModel):
    measurements: List[WithingsMeasurementOut]
    # habit_id (as str) → list of completion date strings
    habit_completions: dict


class HabitStreakDayOut(BaseModel):
    date: str
    streak: int


class HabitBriefingItem(BaseModel):
    name: str
    completed_today: bool


class CalendarMappingItem(BaseModel):
    id: Optional[int] = None
    tag_id: int
    ical_url: str
    name: str = ""


class DiscoveryFeed(BaseModel):
    id: Optional[int] = None
    name: str = ""
    ical_url: str

    model_config = {"from_attributes": True}


class DiscoveryEventOut(BaseModel):
    id: str
    uid: Optional[str] = None     # raw iCal UID, used as feedback key
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    start: datetime
    end: Optional[datetime] = None
    all_day: bool = False
    feed_name: Optional[str] = None
    score: Optional[int] = None   # 1–10 from LLM; None when no interests set
    reason: Optional[str] = None  # one-sentence LLM explanation


class CalendarEvent(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    start: datetime
    end: Optional[datetime] = None
    all_day: bool = False
    section: str
    tag_id: Optional[int] = None
    tag_name: Optional[str] = None
    tag_color: Optional[str] = None
    feed_name: Optional[str] = None
    is_ooo: bool = False


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


class JobSearchResult(BaseModel):
    title: str = ""
    url: str = ""
    content: str = ""


class JobSource(BaseModel):
    type: Literal["card", "text", "tag", "search", "url"]
    card_id: Optional[int] = None
    card_title: Optional[str] = None  # cached title for display if card is later deleted
    tag_id: Optional[int] = None
    tag_name: Optional[str] = None    # cached tag name for display
    tag_color: Optional[str] = None   # cached tag color for display
    content: Optional[str] = None     # for type="text" and type="url"
    # type="search"
    query: Optional[str] = None
    results: Optional[List[JobSearchResult]] = None
    # type="url"
    url: Optional[str] = None
    title: Optional[str] = None       # cached page title for display


class JobCreate(BaseModel):
    title: Optional[str] = None
    prompt: str = ""
    input_sources: List[JobSource] = []


class JobUpdate(BaseModel):
    title: Optional[str] = None
    prompt: Optional[str] = None
    input_sources: Optional[List[JobSource]] = None
    last_output: Optional[str] = None
    output_card_id: Optional[int] = None


class Job(BaseModel):
    id: int
    title: Optional[str] = None
    prompt: str
    input_sources: List[JobSource]
    last_output: Optional[str] = None
    output_card_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    @field_validator('input_sources', mode='before')
    @classmethod
    def parse_input_sources(cls, v):
        if isinstance(v, str):
            import json as _json
            return _json.loads(v) if v else []
        return v or []

    model_config = {"from_attributes": True}


class ThreadMessageRequest(BaseModel):
    content: str


class ThreadContextRequest(BaseModel):
    context: Optional[str] = None


class ThreadOutputRequest(BaseModel):
    output: Optional[str] = None


class AssistRequest(BaseModel):
    card_title: str
    card_description: Optional[str] = None
    context: str  # user-pasted content: emails, messages, documents, etc.
    lat: Optional[float] = None
    lon: Optional[float] = None


class GlobalAssistRequest(BaseModel):
    prompt: str
    section: Optional[str] = None   # "today" | "week" | "month" | "later"
    tag_id: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class BriefingRequest(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    force: bool = False
    today_only: bool = False


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


class CardReorderItem(BaseModel):
    id: int
    section: str
    position: int


class BreakdownCommit(BaseModel):
    subtasks: List[str]
    tag_name: str


class BulkCardItem(BaseModel):
    title: str
    section: str


class BulkCardCreate(BaseModel):
    cards: List[BulkCardItem]


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
