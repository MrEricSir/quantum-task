"""
SQLAlchemy models for the Quantum Task backend.

Date/time storage convention
─────────────────────────────
Three patterns are used, intentionally:

• ``DateTime`` columns holding a UTC instant (e.g. ``Card.created_at``,
  ``Card.completed_at``, ``Card.archived_at``, ``WithingsCredentials.last_synced``,
  ``HealthExperiment.created_at``/``dismissed_at``) — store Python ``datetime``
  objects from ``datetime.now(timezone.utc)``. SQLite strips tzinfo on save, so
  these come back out of the DB as *naive* datetimes; UTC is a convention, not
  something the column enforces. When serialising one of these for the API
  (e.g. in a response schema), re-attach ``tzinfo=timezone.utc`` before calling
  ``.isoformat()`` — a bare naive ISO string has no offset, so `new Date(...)`
  on the frontend parses it as local time and displays the wrong clock time.
  (This was a real bug for ``WithingsCredentials.last_synced`` — fixed by
  attaching tzinfo in the router rather than just here in a comment.)

• ``DateTime`` columns holding a naive *local* wall-clock time (e.g.
  ``Card.scheduled_at``, ``FoodEntry.consumed_at``, ``WorkoutEntry.logged_at``)
  — these represent something the user meant in their own timezone ("3pm
  today"), resolved from the request's local date via ``deps.local_date()``/
  the ``X-Local-Date`` header and stored as-is, with no UTC conversion. The
  frontend's bare `new Date(...)` parsing is correct for these *because* they
  hold local time already — do not "fix" them by attaching UTC tzinfo.

• ``String`` columns holding YYYY-MM-DD dates (e.g. ``HabitCompletion.date``,
  ``HabitStreakDay.date``, ``WithingsMeasurement.date``) — store date-only
  values as plain strings.  SQLite has no native DATE type; using String avoids
  timezone ambiguity for calendar dates that are inherently tz-agnostic (a
  habit "completed on 2026-06-20" is the same regardless of tz).

New models should follow the same convention — and be deliberate about which
of the first two patterns a new timestamp column follows, since they require
opposite handling at the API boundary:
  - UTC instant: use ``datetime.now(timezone.utc)``; attach tzinfo when serialising.
  - Local wall-clock time: resolve from the request's local date; serialise as-is.
  - Use ``String`` (YYYY-MM-DD) for calendar date keys with no time component.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text, Table, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


card_tags = Table(
    "card_tags",
    Base.metadata,
    Column("card_id", Integer, ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    color = Column(String, nullable=False, default="#6b7280")
    is_project = Column(Boolean, nullable=False, default=False)


class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True, index=True)
    # title: headline shown everywhere — required for all cards
    title = Column(String, nullable=False)
    # description: optional text content — short context shown in the detail modal
    #   set by the LLM parser or manually in the add/edit modals
    description = Column(String, nullable=True)
    # section: which board column the card belongs to — today | week | month | later
    section = Column(String, nullable=False, default="today")
    scheduled_at = Column(DateTime, nullable=True)
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # raw_input: verbatim text the user typed into Quick Add — kept for debugging LLM parsing
    raw_input = Column(String, nullable=True)
    recurrence_rule = Column(String, nullable=True)  # daily | weekly | monthly | yearly
    external_id = Column(String, nullable=True, index=True)  # e.g. "github:owner/repo/issues/123"
    body = Column(String, nullable=True)  # legacy column — not used in new code, kept to avoid migration
    spec = Column(Text, nullable=True)    # AI-synthesized implementation spec (markdown)
    updated_at = Column(DateTime, nullable=True)
    archived = Column(Boolean, default=False)
    archived_at = Column(DateTime, nullable=True)
    snoozed_until = Column(String, nullable=True)   # YYYY-MM-DD — suppress from insights until this date
    waiting_reason = Column(String, nullable=True)  # free-text context shown as a badge on the board
    today_since = Column(DateTime, nullable=True)   # when card last entered the 'today' section
    tags   = relationship("Tag", secondary="card_tags", lazy="joined")
    thread = relationship("CardThread", uselist=False, back_populates="card", lazy="joined")

    @property
    def thread_output(self):
        return self.thread.output if self.thread else None


class CardThread(Base):
    """Persistent multi-turn assistant conversation attached to a card."""
    __tablename__ = "card_threads"

    id         = Column(Integer, primary_key=True, index=True)
    card_id    = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, unique=True)
    card       = relationship("Card", back_populates="thread")
    # context: the user-pasted document/email/etc. that persists across turns
    context    = Column(Text, nullable=True)
    # messages: JSON array of {role, content, ts} — the chat history
    messages   = Column(Text, nullable=False, default="[]")
    # output: user-saved result text, shown in the card detail view
    output     = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=True)


class CalendarMapping(Base):
    __tablename__ = "calendar_mappings"

    id = Column(Integer, primary_key=True, index=True)
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    ical_url = Column(String, nullable=False)
    name = Column(String, nullable=False, default="")


class EventDiscoveryFeed(Base):
    """Public iCal feeds used for event discovery (not personal calendars)."""
    __tablename__ = "event_discovery_feeds"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, default="")
    ical_url = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # iCal fetch cache — refreshed every ~3 hours
    last_fetched = Column(DateTime, nullable=True)   # naive UTC
    cached_events = Column(Text, nullable=True)       # JSON-serialized event list


class DiscoveryFeedback(Base):
    """User thumbs-up / thumbs-down on discovered events, used to train the LLM ranker."""
    __tablename__ = "discovery_feedback"

    id = Column(Integer, primary_key=True, index=True)
    event_uid = Column(String, nullable=False, unique=True, index=True)
    event_title = Column(String, nullable=False)
    event_description = Column(String, nullable=True)
    interested = Column(Boolean, nullable=False)  # True = liked, False = not interested
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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
    health_metric = Column(String, nullable=True)   # 'steps' | 'fat_ratio' | None
    health_goal = Column(Float, nullable=True)       # target value (steps count or % body fat)
    tags = relationship("Tag", secondary="habit_tags", lazy="joined")


class HabitCompletion(Base):
    __tablename__ = "habit_completions"
    __table_args__ = (UniqueConstraint("habit_id", "date"),)

    id = Column(Integer, primary_key=True, index=True)
    habit_id = Column(Integer, ForeignKey("habits.id", ondelete="CASCADE"), nullable=False)
    date = Column(String, nullable=False)  # YYYY-MM-DD


class HabitStreakDay(Base):
    """Materialised streak counts — one row per (habit, completed date).
    Only completed days are stored; absence == not completed that day.
    streak = number of consecutive completed days up to and including this date.
    """
    __tablename__ = "habit_streak_days"

    habit_id = Column(Integer, ForeignKey("habits.id", ondelete="CASCADE"), primary_key=True)
    date     = Column(String, primary_key=True)   # YYYY-MM-DD
    streak   = Column(Integer, nullable=False)


class WorkoutEntry(Base):
    """One logged workout session."""
    __tablename__ = "workout_entries"

    id          = Column(Integer, primary_key=True, index=True)
    raw_input   = Column(String, nullable=False)   # original text from user
    type        = Column(String, nullable=False)   # normalized: run|cycle|row|swim|strength|yoga|sport|other
    value       = Column(Float,  nullable=True)    # e.g. 5000 (distance), 185 (weight), 45 (minutes)
    unit        = Column(String, nullable=True)    # e.g. "m", "lbs", "min" — display only, not interpreted
    notes       = Column(String, nullable=True)    # brief LLM note
    logged_at   = Column(DateTime, nullable=False) # when the workout happened
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FoodEntry(Base):
    """One logged food or drink item."""
    __tablename__ = "food_entries"

    id          = Column(Integer, primary_key=True, index=True)
    raw_input   = Column(String, nullable=False)          # original text from user
    name        = Column(String, nullable=False)           # parsed name ("donut", "coffee with milk")
    category    = Column(String, nullable=False)           # "food" | "drink"
    consumed_at = Column(DateTime, nullable=False)         # when eaten/drunk (defaults to now)
    notes       = Column(String, nullable=True)            # brief LLM nutritional assessment
    quality     = Column(Integer, nullable=True)           # 1–10 (10 = highly nutritious)
    calories    = Column(Integer, nullable=True)           # estimated kcal
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class HealthExperiment(Base):
    """One row per weekly experiment — persisted for historical comparison."""
    __tablename__ = "health_experiments"

    id         = Column(Integer, primary_key=True, index=True)
    week       = Column(String, nullable=False)       # ISO week "2026-W26"
    text       = Column(String, nullable=False)        # LLM description shown to user
    hypothesis = Column(String, nullable=True)
    action     = Column(String, nullable=True)         # specific daily action
    needs_habit      = Column(Boolean, default=False)
    habit_id         = Column(Integer, nullable=True)  # linked 🧪 habit (no FK — habit may be archived)
    health_metric    = Column(String, nullable=True)
    health_goal      = Column(Float, nullable=True)
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    dismissed_at = Column(DateTime, nullable=True)
    status      = Column(String, default="active")    # "active" | "dismissed"

    # Outcome metrics — filled on dismiss
    habit_completion_rate = Column(Float, nullable=True)  # 0–1 fraction of days checked
    weight_delta    = Column(Float, nullable=True)   # kg/day during experiment week
    fat_delta       = Column(Float, nullable=True)   # %/day during experiment week
    weight_baseline = Column(Float, nullable=True)   # avg kg/day across prior weeks (control)
    fat_baseline    = Column(Float, nullable=True)

    # Workout-routine experiments (e.g. "row 2mi/day instead of 1mi") -- set at
    # generation time when the experiment targets an established WorkoutEntry
    # type rather than a Withings metric; outcome fields filled on dismiss.
    workout_type           = Column(String, nullable=True)   # WorkoutEntry.type, e.g. "row"
    workout_target_value   = Column(Float, nullable=True)
    workout_unit           = Column(String, nullable=True)
    workout_baseline_avg   = Column(Float, nullable=True)
    workout_experiment_avg = Column(Float, nullable=True)
    workout_baseline_n     = Column(Integer, nullable=True)
    workout_experiment_n   = Column(Integer, nullable=True)
    workout_p              = Column(Float, nullable=True)

    # Food-elimination/reduction experiments (e.g. "cut out coffee this
    # week") -- set at generation time when the experiment targets a
    # regularly-eaten FoodEntry.name rather than a Withings metric or
    # workout/habit routine. No t-test outcome (unlike workout_p) -- adherence
    # is a plain count, not a significance test. weight_delta/fat_delta above
    # are still the outcome signal, but weight_baseline/fat_baseline get
    # overridden at outcome time to the mean across weeks this food was
    # ACTUALLY present (food_baseline_weeks_n of them) rather than every
    # other week regardless of relevance -- falls back to the generic
    # all-other-weeks baseline (food_baseline_weeks_n stays null) when fewer
    # than 2 such weeks exist in the data.
    food_name               = Column(String, nullable=True)   # matched FoodEntry.name, lowercased
    food_baseline_frequency = Column(Float, nullable=True)    # established occurrences/week before
    food_target_frequency   = Column(Float, nullable=True)    # target during the experiment week (0 = eliminate)
    food_experiment_count   = Column(Integer, nullable=True)  # actual occurrences logged during the week
    food_baseline_weeks_n   = Column(Integer, nullable=True)  # weeks the food-specific baseline was averaged over


class WithingsMeasurement(Base):
    """One row per (date, metric) — upserted on each sync."""
    __tablename__ = "withings_measurements"
    __table_args__ = (UniqueConstraint("date", "metric", name="uq_withings_date_metric"),)

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False)    # YYYY-MM-DD
    metric = Column(String, nullable=False)  # 'steps' | 'fat_ratio'
    value = Column(Float, nullable=False)
    synced_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))



class WithingsCredentials(Base):
    """Single-row table for Withings OAuth credentials.

    Replaces the JSON blob previously stored in AppSetting under key
    ``withings_credentials``.  ``last_synced`` replaces the old
    ``withings_last_synced`` AppSetting key.
    """
    __tablename__ = "withings_credentials"

    id              = Column(Integer, primary_key=True)
    access_token    = Column(String, nullable=False)
    token_type      = Column(String, nullable=False, default="Bearer")
    refresh_token   = Column(String, nullable=False)
    userid          = Column(Integer, nullable=False)
    client_id       = Column(String, nullable=False)
    consumer_secret = Column(String, nullable=False)
    expires_in      = Column(Integer, nullable=False, default=10800)
    last_synced     = Column(DateTime, nullable=True)


class PushSubscription(Base):
    """Web Push subscription for a browser/device."""
    __tablename__ = "push_subscriptions"

    id         = Column(Integer, primary_key=True, index=True)
    endpoint   = Column(String, nullable=False, unique=True)
    keys_auth  = Column(String, nullable=False)
    keys_p256dh = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MoodLog(Base):
    """One energy/mood entry per calendar day."""
    __tablename__ = "mood_logs"

    id         = Column(Integer, primary_key=True)
    date       = Column(String,   nullable=False, unique=True)  # YYYY-MM-DD
    energy     = Column(Integer,  nullable=False)               # 1–5
    note       = Column(String,   nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=True)


class AppSetting(Base):
    """Generic key-value store for app-wide settings (e.g. export token)."""
    __tablename__ = "app_settings"

    key   = Column(String, primary_key=True)
    value = Column(String, nullable=False)


class CardEmbedding(Base):
    """Semantic embedding vector for a card, used for natural-language search."""
    __tablename__ = "card_embeddings"

    card_id    = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True)
    embedding  = Column(Text, nullable=False)   # JSON array of floats
    updated_at = Column(DateTime, nullable=False)


class EngineeringItemEmbedding(Base):
    """Semantic embedding vector for a GitHub engineering item."""
    __tablename__ = "engineering_item_embeddings"

    item_id    = Column(Integer, ForeignKey("engineering_items.id", ondelete="CASCADE"), primary_key=True)
    embedding  = Column(Text, nullable=False)   # JSON array of floats
    updated_at = Column(DateTime, nullable=False)


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
    state          = Column(String, nullable=False, default="open")  # "open" | "closed"
    project_name   = Column(String, nullable=True)
    project_status = Column(String, nullable=True)
    synced_at      = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    body           = Column(Text, nullable=True)
    body_updated_at = Column(DateTime, nullable=True)

    comments = relationship("EngineeringItemComment", back_populates="item",
                            cascade="all, delete-orphan", order_by="EngineeringItemComment.created_at")


class EngineeringItemComment(Base):
    """A GitHub issue comment synced from the GitHub API."""
    __tablename__ = "engineering_item_comments"

    id         = Column(Integer, primary_key=True, index=True)
    item_id    = Column(Integer, ForeignKey("engineering_items.id", ondelete="CASCADE"), nullable=False, index=True)
    github_id  = Column(Integer, nullable=False, unique=True)
    author     = Column(String, nullable=True)
    body       = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    item = relationship("EngineeringItem", back_populates="comments")


class BridgeJob(Base):
    """A queued Claude Code bridge job — picked up by the local qtask-bridge agent."""
    __tablename__ = "bridge_jobs"

    id              = Column(Integer, primary_key=True, index=True)
    card_id         = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False)
    status          = Column(String, nullable=False, default="pending")  # pending|running|done|error|stalled
    target_repo     = Column(String, nullable=True)   # "owner/repo" — null means any bridge can claim
    branch_name     = Column(String, nullable=True)   # local branch created by bridge, e.g. qtask/42-fix-login
    agent_name      = Column(String, nullable=True)   # hostname of the machine that ran the job
    worktree_path   = Column(String, nullable=True)   # local filesystem path to the job's git worktree
    spec_snapshot   = Column(Text, nullable=True)
    prompt_snapshot = Column(Text, nullable=True)
    result          = Column(Text, nullable=True)
    output          = Column(Text, nullable=True)   # rolling last ~200 lines of Claude Code stdout
    created_at      = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at      = Column(DateTime, nullable=True)

    card = relationship("Card")


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
