"""
schemas package — domain-split Pydantic models.

Import from this package exactly as before:
    import schemas
    from schemas import Card, ParsedCard, ...

All names are re-exported here for backwards compatibility.
To import by domain directly:
    from schemas.cards import Card, ParsedCard
    from schemas.habits import Habit
"""
from schemas.common import Tag, TagCreate, TagUpdate, TagReplacement
from schemas.cards import (
    CardCreate, CardUpdate, ParseRequest, ParsedCard, BulkParseResponse,
    CardReorderItem, BulkCardItem, BulkCardCreate, BreakdownCommit, Card,
)
from schemas.habits import (
    HabitCreate, HabitUpdate, Habit, HabitStreakDayOut, HabitBriefingItem,
)
from schemas.calendar import (
    CalendarMappingItem, CalendarEvent, DiscoveryFeed, DiscoveryEventOut,
)
from schemas.briefing import BriefingRequest
from schemas.jobs import (
    JobSearchResult, JobSource, JobCreate, JobUpdate, Job,
    ThreadMessageRequest, ThreadContextRequest, ThreadOutputRequest,
    AssistRequest, GlobalAssistRequest, ContextFromRequest,
)
from schemas.withings import WithingsStatus, WithingsMeasurementOut, WithingsHealthData
from schemas.engineering import EngineeringItem

# Legacy Note schemas — kept for any lingering imports
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


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


__all__ = [
    # common
    "Tag", "TagCreate", "TagUpdate", "TagReplacement",
    # cards
    "CardCreate", "CardUpdate", "ParseRequest", "ParsedCard", "BulkParseResponse",
    "CardReorderItem", "BulkCardItem", "BulkCardCreate", "BreakdownCommit", "Card",
    # habits
    "HabitCreate", "HabitUpdate", "Habit", "HabitStreakDayOut", "HabitBriefingItem",
    # calendar
    "CalendarMappingItem", "CalendarEvent", "DiscoveryFeed", "DiscoveryEventOut",
    # briefing
    "BriefingRequest",
    # jobs
    "JobSearchResult", "JobSource", "JobCreate", "JobUpdate", "Job",
    "ThreadMessageRequest", "ThreadContextRequest", "ThreadOutputRequest",
    "AssistRequest", "GlobalAssistRequest", "ContextFromRequest",
    # withings
    "WithingsStatus", "WithingsMeasurementOut", "WithingsHealthData",
    # engineering
    "EngineeringItem",
    # legacy
    "NoteCreate", "NoteUpdate", "Note",
]
