"""
Tests for github_sync.sync()'s card lifecycle transitions -- specifically the
"issue/PR closed on GitHub" path, which completes AND archives any linked card
in the same step. That combination is what silently dropped GitHub-ticket
tasks out of the Telegram evening summary (its completed-today query used to
filter on archived == False) -- see telegram/scheduler.py's
check_evening_summary and tests/test_telegram.py's TestCheckEveningSummary.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import github_sync


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

import pytest


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=engine)
    yield
    models.Base.metadata.drop_all(bind=engine)


EXTERNAL_ID = "github:owner/repo/issues/1"
NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _fake_github_item():
    return {
        "title": "Test issue",
        "html_url": "https://github.com/owner/repo/issues/1",
        "number": 1,
        "body": "Issue body",
        "updated_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _sync_with_open_item():
    """First sync: the item is open on GitHub, so this just creates the
    tracked EngineeringItem (state=open) -- no linked card yet."""
    with patch("github_sync._fetch_items", return_value=[_fake_github_item()]), \
         patch("github_sync._fetch_comments", return_value=[]), \
         patch("github_sync._fetch_project_statuses", return_value={}), \
         patch("embeddings.upsert_eng_bg", return_value=None):
        with TestSession() as db:
            github_sync.sync(db)


def _sync_with_item_closed():
    """Second sync: the item no longer appears in the open set (GitHub says
    it's closed), which is what triggers the complete+archive branch."""
    with patch("github_sync._fetch_items", return_value=[]), \
         patch("github_sync._fetch_project_statuses", return_value={}), \
         patch("embeddings.upsert_eng_bg", return_value=None):
        with TestSession() as db:
            return github_sync.sync(db)


def _make_linked_card(completed=False, completed_at=None):
    with TestSession() as db:
        card = models.Card(
            title="Fix the bug", section="today", position=0,
            external_id=EXTERNAL_ID, completed=completed, completed_at=completed_at,
        )
        db.add(card)
        db.commit()
        db.refresh(card)
        return card.id


class TestClosedIssueCompletesAndArchivesLinkedCard:

    def test_marks_the_engineering_item_closed(self):
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            db.commit()
        _sync_with_open_item()

        result = _sync_with_item_closed()

        assert result["closed"] == 1
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(external_id=EXTERNAL_ID).first()
            assert item.state == "closed"

    def test_completes_and_archives_the_linked_card(self):
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            db.commit()
        _sync_with_open_item()
        card_id = _make_linked_card()

        _sync_with_item_closed()

        with TestSession() as db:
            card = db.query(models.Card).filter_by(id=card_id).first()
            assert card.completed is True
            assert card.completed_at is not None
            assert card.archived is True
            assert card.archived_at is not None

    def test_does_not_overwrite_completed_at_for_a_card_already_marked_done(self):
        """If the card was already completed (e.g. via the In Progress -> Done
        board transition) before its issue/PR was actually closed on GitHub,
        the close-sync should still archive it but leave the original
        completion timestamp alone."""
        original_completed_at = datetime(2026, 5, 20, 9, 0, 0)
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            db.commit()
        _sync_with_open_item()
        card_id = _make_linked_card(completed=True, completed_at=original_completed_at)

        _sync_with_item_closed()

        with TestSession() as db:
            card = db.query(models.Card).filter_by(id=card_id).first()
            assert card.completed is True
            assert card.completed_at == original_completed_at
            assert card.archived is True

    def test_does_not_touch_a_card_with_no_matching_external_id(self):
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            db.add(models.Card(title="Unrelated task", section="today", position=0))
            db.commit()
        _sync_with_open_item()

        _sync_with_item_closed()

        with TestSession() as db:
            card = db.query(models.Card).filter_by(title="Unrelated task").first()
            assert card.completed is False
            assert card.archived is False

    def test_already_archived_linked_card_is_left_alone(self):
        """The close-sync query filters on archived == False -- a card that
        was already archived some other way shouldn't be touched again."""
        old_completed_at = datetime(2026, 1, 1, 8, 0, 0)
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            db.commit()
        _sync_with_open_item()
        with TestSession() as db:
            db.add(models.Card(
                title="Already archived", section="today", position=0,
                external_id=EXTERNAL_ID, completed=True,
                completed_at=old_completed_at, archived=True,
            ))
            db.commit()

        _sync_with_item_closed()

        with TestSession() as db:
            card = db.query(models.Card).filter_by(title="Already archived").first()
            assert card.completed_at == old_completed_at
