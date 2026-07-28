from pydantic import BaseModel
from typing import Literal, Optional


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
