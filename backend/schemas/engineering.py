from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class EngineeringItem(BaseModel):
    id: int
    external_id: str
    title: str
    item_type: str
    repo: str
    number: int
    url: str
    state: str
    project_name: Optional[str] = None
    project_status: Optional[str] = None
    synced_at: datetime

    model_config = {"from_attributes": True}
