from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TripCreate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[str] = None  # defaults to the request's local date


class TripUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[str] = None


class Trip(BaseModel):
    id: int
    name: Optional[str] = None
    start_date: str
    end_date: Optional[str] = None
    created_at: datetime
    retrospective_sent: bool = False
    retrospective_skipped: bool = False

    model_config = {"from_attributes": True}


class TripEndResult(BaseModel):
    trip: Trip
    retrospective: Optional[str] = None
