from pydantic import BaseModel, field_validator
from typing import List, Literal, Optional
from datetime import datetime


class JobSearchResult(BaseModel):
    title: str = ""
    url: str = ""
    content: str = ""


class JobSource(BaseModel):
    type: Literal["card", "text", "tag", "search", "url"]
    card_id: Optional[int] = None
    card_title: Optional[str] = None
    tag_id: Optional[int] = None
    tag_name: Optional[str] = None
    tag_color: Optional[str] = None
    content: Optional[str] = None
    query: Optional[str] = None
    results: Optional[List[JobSearchResult]] = None
    url: Optional[str] = None
    title: Optional[str] = None


class JobCreate(BaseModel):
    title: Optional[str] = None
    prompt: str = ""
    input_sources: List[JobSource] = []


class JobUpdate(BaseModel):
    title: Optional[str] = None
    prompt: Optional[str] = None
    input_sources: Optional[List[JobSource]] = None
    last_output: Optional[str] = None
    output_card_id: Optional[int] = None


class Job(BaseModel):
    id: int
    title: Optional[str] = None
    prompt: str
    input_sources: List[JobSource]
    last_output: Optional[str] = None
    output_card_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    @field_validator('input_sources', mode='before')
    @classmethod
    def parse_input_sources(cls, v):
        if isinstance(v, str):
            import json as _json
            return _json.loads(v) if v else []
        return v or []

    model_config = {"from_attributes": True}


class ThreadMessageRequest(BaseModel):
    content: str


class ThreadContextRequest(BaseModel):
    context: Optional[str] = None


class ThreadOutputRequest(BaseModel):
    output: Optional[str] = None


class AssistRequest(BaseModel):
    card_title: str
    card_description: Optional[str] = None
    context: str
    lat: Optional[float] = None
    lon: Optional[float] = None


class GlobalAssistRequest(BaseModel):
    prompt: str
    section: Optional[str] = None
    tag_id: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class ContextFromRequest(BaseModel):
    source: Literal["section", "tag", "similar"]
    section: Optional[str] = None
    tag_id: Optional[int] = None
