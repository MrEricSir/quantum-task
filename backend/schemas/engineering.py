from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from schemas.common import Tag


class EngineeringItemComment(BaseModel):
    id: int
    github_id: int
    author: Optional[str] = None
    body: str
    created_at: datetime
    updated_at: datetime
    comment_type: str = "issue_comment"
    diff_path: Optional[str] = None
    diff_line: Optional[int] = None

    model_config = {"from_attributes": True}


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
    body: Optional[str] = None
    body_updated_at: Optional[datetime] = None
    comments: list[EngineeringItemComment] = []
    # Computed from repo-tag rules (not a DB relationship) — see routers.engineering.
    tags: list[Tag] = []

    model_config = {"from_attributes": True}
