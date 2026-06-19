"""Shared FastAPI dependencies and app-wide configuration."""
import hmac as _hmac
import os
from datetime import date

from fastapi import Request
from openai import OpenAI
from sqlalchemy.orm import Session

from database import SessionLocal

# ── LLM config ───────────────────────────────────────────────────────────────

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY  = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL    = os.getenv("LLM_MODEL", os.getenv("OLLAMA_MODEL", "llama3.2"))

_llm_client: OpenAI | None = None


def llm_client() -> OpenAI:
    """Return the shared OpenAI-compatible LLM client (created once)."""
    global _llm_client
    if _llm_client is None:
        _llm_client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    return _llm_client


# ── Auth config ───────────────────────────────────────────────────────────────

AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")
SESSION_TOKEN = (
    _hmac.new(AUTH_PASSWORD.encode(), b"session-v1", "sha256").hexdigest()
    if AUTH_PASSWORD else ""
)

# ── DB dependency ─────────────────────────────────────────────────────────────


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Request helpers ───────────────────────────────────────────────────────────


def local_date(request: Request) -> date:
    """Return the client's local date from the X-Local-Date header.

    The frontend sends its local YYYY-MM-DD date on every request so that
    section assignment, habit resets, and filtering all use the user's clock
    rather than the server's (which is UTC on Cloud Run).
    Falls back to date.today() when the header is absent (e.g. direct API calls).
    """
    raw = request.headers.get("X-Local-Date", "")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.today()
