from pydantic import BaseModel
from typing import Optional


class BriefingRequest(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    force: bool = False
    today_only: bool = False
