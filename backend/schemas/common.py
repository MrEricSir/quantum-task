from pydantic import BaseModel
from typing import List, Optional


class Tag(BaseModel):
    id: int
    name: str
    color: str
    is_project: bool = False

    model_config = {"from_attributes": True}


class TagCreate(BaseModel):
    name: str
    color: str = "#6b7280"
    is_project: bool = False


class TagUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    is_project: Optional[bool] = None


class TagReplacement(BaseModel):
    new_tag_id: int


class NavPreferences(BaseModel):
    order: List[str]
    default_page: str
