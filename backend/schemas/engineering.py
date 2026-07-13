from pydantic import BaseModel
from datetime import datetime


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
