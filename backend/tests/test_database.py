"""
Tests for database.py's SQLite foreign-key enforcement.

SQLite disables FK enforcement per-connection by default -- without the
`PRAGMA foreign_keys=ON` connect-event listener in database.py, every
ForeignKey(..., ondelete="CASCADE"/"SET NULL") in models.py is pure
documentation, never actually enforced. These tests prove it's both ON and
actually doing something (real cascade behavior, real rejection of a
dangling reference), not just that the pragma call doesn't error.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database  # noqa: F401 -- importing registers the connect-event listener on Engine
import models

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=engine)
    yield
    models.Base.metadata.drop_all(bind=engine)


def test_foreign_keys_pragma_is_on():
    with TestSession() as db:
        value = db.execute(text("PRAGMA foreign_keys")).scalar()
    assert value == 1


def test_deleting_a_habit_cascades_to_its_completions_and_streak_days():
    with TestSession() as db:
        habit = models.Habit(name="Meditate")
        db.add(habit)
        db.flush()
        db.add(models.HabitCompletion(habit_id=habit.id, date="2026-11-01"))
        db.add(models.HabitStreakDay(habit_id=habit.id, date="2026-11-01", streak=1))
        db.commit()
        habit_id = habit.id

        db.delete(habit)
        db.commit()

        assert db.query(models.HabitCompletion).filter_by(habit_id=habit_id).count() == 0
        assert db.query(models.HabitStreakDay).filter_by(habit_id=habit_id).count() == 0


def test_deleting_a_card_cascades_to_its_bridge_jobs():
    with TestSession() as db:
        card = models.Card(title="Fix bug", section="today", position=0)
        db.add(card)
        db.flush()
        db.add(models.BridgeJob(card_id=card.id, status="pending", created_at=datetime.now(timezone.utc)))
        db.commit()
        card_id = card.id

        db.delete(card)
        db.commit()

        assert db.query(models.BridgeJob).filter_by(card_id=card_id).count() == 0


def test_deleting_a_tag_cascades_to_its_calendar_mappings():
    with TestSession() as db:
        tag = models.Tag(name="work")
        db.add(tag)
        db.flush()
        db.add(models.CalendarMapping(tag_id=tag.id, ical_url="https://example.com/cal.ics", name="Work"))
        db.commit()
        tag_id = tag.id

        db.delete(tag)
        db.commit()

        assert db.query(models.CalendarMapping).filter_by(tag_id=tag_id).count() == 0


def test_inserting_a_dangling_foreign_key_is_rejected():
    with TestSession() as db:
        db.add(models.BridgeJob(card_id=999999, status="pending", created_at=datetime.now(timezone.utc)))
        with pytest.raises(IntegrityError):
            db.commit()
