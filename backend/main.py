import asyncio
import hmac as _hmac
import os
from contextlib import asynccontextmanager

# Load .env for local development (no-op in production where env vars are injected)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

import models
import push as push_lib
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command
from database import SessionLocal, engine
from deps import AUTH_PASSWORD, SESSION_TOKEN

from routers import auth, engineering, push, tags, jobs, habits, calendar, cards, briefing, withings, search

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:5173")


# ── Auth middleware ───────────────────────────────────────────────────────────

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not AUTH_PASSWORD:
            return await call_next(request)
        path = request.url.path
        if (
            not path.startswith("/api/")
            or path.startswith("/api/auth/")
            or path == "/api/calendar/export.ics"
            or path == "/api/withings/callback"
        ):
            return await call_next(request)
        token = request.cookies.get("session", "")
        if not _hmac.compare_digest(token, SESSION_TOKEN):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


# ── Startup migrations ────────────────────────────────────────────────────────

def _run_startup_migrations():
    # Run Alembic migrations to head
    alembic_cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "alembic"))
    try:
        alembic_command.upgrade(alembic_cfg, "head")
    except Exception as e:
        print(f"[alembic] migration warning: {e}")

    # Migrate calendar_mappings to current schema (v1 had calendar_id; v2 lacked name column)
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT calendar_id FROM calendar_mappings LIMIT 1"))
            conn.execute(text("DROP TABLE calendar_mappings"))
            conn.commit()
        except Exception:
            pass

    with engine.connect() as conn:
        table_exists = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='calendar_mappings'"
        )).fetchone()
        if table_exists:
            try:
                conn.execute(text("SELECT name FROM calendar_mappings LIMIT 1"))
            except Exception:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS calendar_mappings_new (
                        id      INTEGER PRIMARY KEY AUTOINCREMENT,
                        tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                        ical_url TEXT NOT NULL,
                        name    TEXT NOT NULL DEFAULT ''
                    )
                """))
                conn.execute(text("""
                    INSERT INTO calendar_mappings_new (id, tag_id, ical_url, name)
                    SELECT id, tag_id, ical_url, '' FROM calendar_mappings
                """))
                conn.execute(text("DROP TABLE calendar_mappings"))
                conn.execute(text("ALTER TABLE calendar_mappings_new RENAME TO calendar_mappings"))
                conn.commit()

    # Create all tables not yet handled by Alembic
    models.Base.metadata.create_all(bind=engine)

    # Legacy column additions (idempotent — all wrapped in try/except)
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE cards ADD COLUMN completed_at DATETIME",
            "ALTER TABLE cards ADD COLUMN raw_input TEXT",
            "ALTER TABLE cards ADD COLUMN recurrence_rule TEXT",
            "ALTER TABLE cards ADD COLUMN external_id TEXT",
            "ALTER TABLE habits ADD COLUMN archived BOOLEAN DEFAULT 0",
            "ALTER TABLE habits ADD COLUMN archived_at DATETIME",
            "ALTER TABLE notes ADD COLUMN archived BOOLEAN DEFAULT 0",
            "ALTER TABLE notes ADD COLUMN archived_at DATETIME",
            "ALTER TABLE cards ADD COLUMN body TEXT",
            "ALTER TABLE cards ADD COLUMN updated_at DATETIME",
            "ALTER TABLE cards ADD COLUMN archived BOOLEAN DEFAULT 0",
            "ALTER TABLE cards ADD COLUMN archived_at DATETIME",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass

        # Migrate briefing_cache to per-section schema (old schema had today_text/week_text columns)
        try:
            conn.execute(text("SELECT today_text FROM briefing_cache LIMIT 1"))
            conn.execute(text("DROP TABLE briefing_cache"))
            conn.commit()
        except Exception:
            pass

    # Seed default tags
    with SessionLocal() as db:
        for name, color in [("personal", "#8b5cf6"), ("work", "#3b82f6")]:
            if not db.query(models.Tag).filter_by(name=name).first():
                db.add(models.Tag(name=name, color=color))
        db.commit()

    # Migrate notes → cards (section="none") — idempotent via app_settings flag
    with SessionLocal() as db:
        if not db.query(models.AppSetting).filter_by(key="notes_migrated_v1").first():
            for note in db.query(models.Note).all():
                first_line = note.content.split('\n')[0][:120].strip() if note.content else ""
                title = note.title or first_line or "Untitled"
                db_card = models.Card(
                    title=title,
                    body=note.content or None,
                    section="none",
                    position=0,
                    archived=note.archived,
                    archived_at=note.archived_at,
                    created_at=note.created_at,
                    updated_at=note.updated_at,
                )
                db_card.tags = list(note.tags)
                db.add(db_card)
            db.add(models.AppSetting(key="notes_migrated_v1", value="1"))
            db.commit()

    # Ensure VAPID keys exist
    with SessionLocal() as db:
        push_lib.ensure_vapid_keys(db)

    # Populate habit_streak_days for all habits (one-time, guarded by flag)
    with SessionLocal() as db:
        if not db.query(models.AppSetting).filter_by(key="streak_days_v1").first():
            from streak import recompute_all_habits
            recompute_all_habits(db)
            db.add(models.AppSetting(key="streak_days_v1", value="1"))
            db.commit()


# ── Push notification scheduler ───────────────────────────────────────────────

_push_sent: set[str] = set()


def _fire_due_push_notifications() -> None:
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=15)
    with SessionLocal() as db:
        cards = db.query(models.Card).filter(
            models.Card.scheduled_at.isnot(None),
            models.Card.completed == False,   # noqa: E712
            models.Card.scheduled_at >= now,
            models.Card.scheduled_at <= cutoff,
        ).all()
        if not cards:
            return
        subs = db.query(models.PushSubscription).all()
        if not subs:
            return
        private_key = db.query(models.AppSetting).filter_by(key="vapid_private_key").first()
        if not private_key or not private_key.value:
            return
        priv = private_key.value
        dead_endpoints = []
        for card in cards:
            key = f"{card.id}:{card.scheduled_at.isoformat()}"
            if key in _push_sent:
                continue
            _push_sent.add(key)
            mins = round((card.scheduled_at.replace(tzinfo=timezone.utc) - now).total_seconds() / 60)
            payload = {
                "title": card.title,
                "body": "Due now" if mins <= 1 else f"Due in {mins} minute{'s' if mins != 1 else ''}",
                "tag": key,
                "todoId": card.id,
            }
            for sub in subs:
                alive = push_lib.send_notification(sub, payload, priv)
                if not alive:
                    dead_endpoints.append(sub.endpoint)
        for ep in set(dead_endpoints):
            db.query(models.PushSubscription).filter_by(endpoint=ep).delete()
        db.commit()


async def _push_scheduler() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.get_event_loop().run_in_executor(None, _fire_due_push_notifications)
        except Exception as e:
            print(f"[push] scheduler error: {e}")


async def _withings_scheduler() -> None:
    """Sync Withings data every 2 hours if credentials are stored."""
    await asyncio.sleep(7200)  # don't run immediately on startup
    while True:
        try:
            from routers.withings import do_sync
            with SessionLocal() as db:
                row = db.query(models.AppSetting).filter_by(key="withings_credentials").first()
                if row:
                    do_sync(db)
        except Exception as e:
            print(f"[withings] scheduler error: {e}")
        await asyncio.sleep(7200)


@asynccontextmanager
async def lifespan(app):
    _run_startup_migrations()
    task = asyncio.create_task(_push_scheduler())
    wtask = asyncio.create_task(_withings_scheduler())
    yield
    task.cancel()
    wtask.cancel()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Todo Dashboard API", lifespan=lifespan)

app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(engineering.router)
app.include_router(push.router)
app.include_router(tags.router)
app.include_router(jobs.router)
app.include_router(habits.router)
app.include_router(calendar.router)
app.include_router(cards.router)
app.include_router(briefing.router)
app.include_router(withings.router)
app.include_router(search.router)

# Serve bundled frontend for all non-API routes (must be last).
# Using an explicit catch-all route instead of StaticFiles mount to avoid
# Starlette routing ambiguity where Mount("/") can shadow specific API routes.
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = os.path.join(_static_dir, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_static_dir, "index.html"))
