"""Shared FastAPI dependencies and app-wide configuration."""
import os
from datetime import date, datetime, timedelta

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


def reasoning_kwargs() -> dict:
    """Extra kwargs for chat.completions.create() calls, spread in as **reasoning_kwargs().

    reasoning_effort="low" stops a reasoning-capable model (Groq's gpt-oss family is the one
    actually in use) from burning its whole max_tokens budget on internal chain-of-thought
    before ever writing the real answer -- see routers/correlations.py's _generate_experiment
    docstring for the production incident that motivated this. Only included when LLM_MODEL is
    actually a reasoning model: passing it to a backend/model with no concept of "thinking"
    (e.g. local Ollama models like llama3.2) doesn't get silently ignored the way an unknown
    kwarg normally would -- Ollama's OpenAI-compatible endpoint rejects the whole request with
    a 400 ("does not support thinking"), which is what broke local dev LLM calls entirely once
    reasoning_effort started getting passed unconditionally everywhere.
    """
    if "gpt-oss" in LLM_MODEL:
        return {"reasoning_effort": "low"}
    return {}


# ── Auth config ───────────────────────────────────────────────────────────────
# The session cookie's value (app_setting_keys.SESSION_SECRET, see settings.py and
# routers/auth.py) is a random secret stored in the DB, not derived from AUTH_PASSWORD --
# that makes logout a real revocation (rotating it invalidates every outstanding
# cookie) independent of the login password.

AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")

# ── DB dependency ─────────────────────────────────────────────────────────────


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Request helpers ───────────────────────────────────────────────────────────


def utc_offset_minutes(request: Request) -> int:
    """Return the client's UTC offset in minutes from the X-UTC-Offset header.

    JavaScript's Date.getTimezoneOffset() returns the offset as UTC-local in minutes,
    so UTC+10 → -600 and UTC-5 → +300. Falls back to 0 (UTC) when absent.
    """
    raw = request.headers.get("X-UTC-Offset", "")
    try:
        return int(raw)
    except ValueError:
        return 0


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


def to_local_date(dt: datetime, tz_offset_minutes: int) -> date:
    """Convert a UTC-instant DateTime column's value (Card.created_at,
    Card.completed_at, Card.archived_at, Habit.created_at, HealthExperiment.created_at/
    dismissed_at, WithingsCredentials.last_synced -- see models.py's docstring for the
    full enumerated list) to the client's local calendar date, using the same
    JS-convention offset as utc_offset_minutes().

    This is the ONE place this conversion should happen. Comparing a UTC-instant
    column's raw `.date()` against a local "today" (from local_date()) has been the
    single most-repeated timezone bug in this codebase -- found and fixed independently
    in gcal.py (twice), correlations.py, briefing/context.py, telegram/scheduler.py, and
    insights.py, because each call site re-derived (or forgot) the same offset math.
    tests/test_timezone_conventions.py mechanically checks that no other call site
    bypasses this helper for one of the enumerated UTC-instant columns."""
    naive_utc = dt.replace(tzinfo=None) if dt.tzinfo else dt
    return (naive_utc - timedelta(minutes=tz_offset_minutes)).date()
