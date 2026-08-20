from pydantic import BaseModel
from typing import Optional


class WithingsStatus(BaseModel):
    connected: bool
    last_synced: Optional[str] = None


class WithingsMeasurementOut(BaseModel):
    id: int
    date: str
    metric: str
    value: float
    source: str = "withings"


class WithingsHealthData(BaseModel):
    measurements: list
    habit_completions: dict
