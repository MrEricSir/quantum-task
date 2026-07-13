from pydantic import BaseModel
from typing import Optional


class WithingsStatus(BaseModel):
    connected: bool
    last_synced: Optional[str] = None


class WithingsMeasurementOut(BaseModel):
    date: str
    metric: str
    value: float


class WithingsHealthData(BaseModel):
    measurements: list
    habit_completions: dict
