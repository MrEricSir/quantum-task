from pydantic import BaseModel
from typing import Optional


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
