from sqlalchemy import Column, Integer, String, Boolean, DateTime, Table, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


todo_tags = Table(
    "todo_tags",
    Base.metadata,
    Column("todo_id", Integer, ForeignKey("todos.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    color = Column(String, nullable=False, default="#6b7280")


class Todo(Base):
    __tablename__ = "todos"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    section = Column(String, nullable=False, default="today")  # today | week | month | later
    scheduled_at = Column(DateTime, nullable=True)
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    raw_input = Column(String, nullable=True)
    recurrence_rule = Column(String, nullable=True)  # daily | weekly | monthly | yearly
    external_id = Column(String, nullable=True, index=True)  # e.g. "github:owner/repo/issues/123"
    tags = relationship("Tag", secondary="todo_tags", lazy="joined")


class CalendarMapping(Base):
    __tablename__ = "calendar_mappings"

    id = Column(Integer, primary_key=True, index=True)
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    ical_url = Column(String, nullable=False)
    name = Column(String, nullable=False, default="")


habit_tags = Table(
    "habit_tags",
    Base.metadata,
    Column("habit_id", Integer, ForeignKey("habits.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Habit(Base):
    __tablename__ = "habits"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    archived = Column(Boolean, default=False)
    archived_at = Column(DateTime, nullable=True)
    tags = relationship("Tag", secondary="habit_tags", lazy="joined")


class HabitCompletion(Base):
    __tablename__ = "habit_completions"
    __table_args__ = (UniqueConstraint("habit_id", "date"),)

    id = Column(Integer, primary_key=True, index=True)
    habit_id = Column(Integer, ForeignKey("habits.id", ondelete="CASCADE"), nullable=False)
    date = Column(String, nullable=False)  # YYYY-MM-DD


note_tags = Table(
    "note_tags",
    Base.metadata,
    Column("note_id", Integer, ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=True)
    content = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    archived = Column(Boolean, default=False)
    archived_at = Column(DateTime, nullable=True)
    tags = relationship("Tag", secondary="note_tags", lazy="joined")


class PushSubscription(Base):
    """Web Push subscription for a browser/device."""
    __tablename__ = "push_subscriptions"

    id         = Column(Integer, primary_key=True, index=True)
    endpoint   = Column(String, nullable=False, unique=True)
    keys_auth  = Column(String, nullable=False)
    keys_p256dh = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AppSetting(Base):
    """Generic key-value store for app-wide settings (e.g. export token)."""
    __tablename__ = "app_settings"

    key   = Column(String, primary_key=True)
    value = Column(String, nullable=False)


class EngineeringItem(Base):
    """Read-only mirror of GitHub issues / PRs assigned to the user."""
    __tablename__ = "engineering_items"

    id          = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, nullable=False, unique=True, index=True)
    title       = Column(String, nullable=False)
    item_type   = Column(String, nullable=False)   # "pr" | "issue"
    repo        = Column(String, nullable=False)   # "owner/repo"
    number      = Column(Integer, nullable=False)
    url         = Column(String, nullable=False)
    state       = Column(String, nullable=False, default="open")  # "open" | "closed"
    synced_at   = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class BriefingCache(Base):
    """One row per section ('today' or 'week'), keyed on section+content hash.

    The primary key is '{section}:{content_hash}' so db.merge() works cleanly.
    """
    __tablename__ = "briefing_cache"

    id           = Column(String, primary_key=True)  # f"{section}:{content_hash}"
    section      = Column(String, nullable=False)     # "today" | "week"
    content_hash = Column(String, nullable=False)
    text         = Column(String, nullable=False)
    weather_json = Column(String, nullable=True)      # only populated for section="today"
    created_at   = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
