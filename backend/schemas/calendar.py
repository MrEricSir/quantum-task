from pydantic import BaseModel
from typing import Optional
from datetime import datetime


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


class DiscoveryFeed(BaseModel):
    id: Optional[int] = None
    name: str = ""
    ical_url: str

    model_config = {"from_attributes": True}


class DiscoveryEventOut(BaseModel):
    id: str
    uid: Optional[str] = None
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    start: datetime
    end: Optional[datetime] = None
    all_day: bool = False
    feed_name: Optional[str] = None
    score: Optional[int] = None
    reason: Optional[str] = None
