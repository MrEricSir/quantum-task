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
import app_setting_keys as setting_keys
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command
from database import SessionLocal, engine
from deps import AUTH_PASSWORD, SESSION_TOKEN

from routers import auth, engineering, push, tags, jobs, habits, calendar, cards, withings, search, insights, correlations, food, discovery, assist, mood
import briefing as briefing_pkg
import telegram as telegram_pkg

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
            or path == "/api/telegram/webhook"
        ):
            return await call_next(request)
        # Session cookie (browser)
        token = request.cookies.get("session", "")
        if _hmac.compare_digest(token, SESSION_TOKEN):
            return await call_next(request)
        # Bearer token (API clients e.g. iOS Shortcuts)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer = auth_header[7:]
            if _hmac.compare_digest(bearer, AUTH_PASSWORD):
                return await call_next(request)
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)


# ── Startup migrations ────────────────────────────────────────────────────────

def _patch_pre_alembic_schema():
    """Fix schema states that pre-date Alembic and can't be expressed as simple ADD COLUMN."""
    with engine.connect() as conn:
        # calendar_mappings v1 had a calendar_id column — drop and let create_all rebuild it
        try:
            conn.execute(text("SELECT calendar_id FROM calendar_mappings LIMIT 1"))
            conn.execute(text("DROP TABLE calendar_mappings"))
            conn.commit()
            print("[startup] dropped legacy calendar_mappings (v1 schema with calendar_id)")
        except Exception:
            pass  # not v1 schema — expected

        # calendar_mappings v2 was missing the name column — recreate with correct schema
        table_exists = conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='calendar_mappings'"
        )).fetchone()
        if table_exists:
            try:
                conn.execute(text("SELECT name FROM calendar_mappings LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS calendar_mappings_new (
                            id       INTEGER PRIMARY KEY AUTOINCREMENT,
                            tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                            ical_url TEXT NOT NULL,
                            name     TEXT NOT NULL DEFAULT ''
                        )
                    """))
                    conn.execute(text("""
                        INSERT INTO calendar_mappings_new (id, tag_id, ical_url, name)
                        SELECT id, tag_id, ical_url, '' FROM calendar_mappings
                    """))
                    conn.execute(text("DROP TABLE calendar_mappings"))
                    conn.execute(text("ALTER TABLE calendar_mappings_new RENAME TO calendar_mappings"))
                    conn.commit()
                    print("[startup] migrated calendar_mappings to v3 schema (added name column)")
                except Exception as e:
                    print(f"[startup] calendar_mappings migration failed: {e}")

        # briefing_cache v1 had today_text/week_text columns — drop so create_all rebuilds it
        try:
            conn.execute(text("SELECT today_text FROM briefing_cache LIMIT 1"))
            conn.execute(text("DROP TABLE briefing_cache"))
            conn.commit()
            print("[startup] dropped legacy briefing_cache (pre-section schema)")
        except Exception:
            pass  # not old schema — expected


def _seed_defaults():
    """Seed default tags if they don't exist yet."""
    try:
        with SessionLocal() as db:
            for name, color in [("personal", "#8b5cf6"), ("work", "#3b82f6")]:
                if not db.query(models.Tag).filter_by(name=name).first():
                    db.add(models.Tag(name=name, color=color))
                    print(f"[startup] seeded default tag: {name}")
            db.commit()
    except Exception as e:
        print(f"[startup] seed_defaults failed: {e}")


def _backfill_streak_days():
    """One-time backfill: populate habit_streak_days for all existing habits."""
    try:
        with SessionLocal() as db:
            if db.query(models.AppSetting).filter_by(key=setting_keys.STREAK_DAYS_V1).first():
                return
            from streak import recompute_all_habits
            recompute_all_habits(db)
            db.add(models.AppSetting(key=setting_keys.STREAK_DAYS_V1, value="1"))
            db.commit()
            print("[startup] backfilled habit streak days")
    except Exception as e:
        print(f"[startup] backfill_streak_days failed: {e}")


def _ensure_vapid_keys():
    """Ensure VAPID keys exist for push notifications."""
    try:
        with SessionLocal() as db:
            push_lib.ensure_vapid_keys(db)
    except Exception as e:
        print(f"[startup] ensure_vapid_keys failed: {e}")


def _run_startup_migrations():
    # 1. Run Alembic migrations to head
    alembic_cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "alembic"))
    try:
        alembic_command.upgrade(alembic_cfg, "head")
    except Exception as e:
        print(f"[startup] alembic upgrade failed: {e}")

    # 2. Fix schema states that pre-date Alembic
    _patch_pre_alembic_schema()

    # 3. Create any tables not yet managed by Alembic
    models.Base.metadata.create_all(bind=engine)

    # 4. Seed default data
    _seed_defaults()

    # 5. One-time data migrations (each guarded by an AppSetting flag)
    _backfill_streak_days()
    _ensure_vapid_keys()


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
        private_key = db.query(models.AppSetting).filter_by(key=setting_keys.VAPID_PRIVATE_KEY).first()
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
                "cardId": card.id,
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
                if db.query(models.WithingsCredentials).first():
                    do_sync(db)
        except Exception as e:
            print(f"[withings] scheduler error: {e}")
        await asyncio.sleep(7200)


def _expire_stale_experiments() -> None:
    """Archive habits and dismiss active experiments from previous weeks."""
    try:
        from datetime import date
        from routers.correlations import auto_expire_stale_experiments, _current_isoweek
        with SessionLocal() as db:
            auto_expire_stale_experiments(db, _current_isoweek(), date.today())
    except Exception as e:
        print(f"[experiments] cleanup error: {e}")


async def _experiment_cleanup_scheduler() -> None:
    """Run experiment cleanup once a day so stale habits are archived promptly."""
    while True:
        await asyncio.sleep(86400)  # 24 hours
        await asyncio.get_event_loop().run_in_executor(None, _expire_stale_experiments)


# ── Telegram briefing scheduler ───────────────────────────────────────────────

def _check_telegram_briefing() -> None:
    from telegram.scheduler import check_all
    with SessionLocal() as db:
        results = check_all(db)
        if not results.get("skipped"):
            print(f"[telegram] {results}")


async def _telegram_briefing_scheduler() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.get_event_loop().run_in_executor(None, _check_telegram_briefing)
        except Exception as e:
            print(f"[telegram] scheduler error: {e}")


@asynccontextmanager
async def lifespan(app):
    _run_startup_migrations()
    _expire_stale_experiments()  # run once at startup to catch any backlog
    task  = asyncio.create_task(_push_scheduler())
    wtask = asyncio.create_task(_withings_scheduler())
    etask = asyncio.create_task(_experiment_cleanup_scheduler())
    ttask = asyncio.create_task(_telegram_briefing_scheduler())
    yield
    task.cancel()
    wtask.cancel()
    etask.cancel()
    ttask.cancel()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Quantum Task API", lifespan=lifespan)

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
app.include_router(briefing_pkg.router)
app.include_router(withings.router)
app.include_router(search.router)
app.include_router(insights.router)
app.include_router(correlations.router)
app.include_router(food.router)
app.include_router(discovery.router)
app.include_router(telegram_pkg.router)
app.include_router(assist.router)
app.include_router(mood.router)

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
