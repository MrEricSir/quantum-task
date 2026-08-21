"""
Tests for GitHub comment sync and GitHub context injection.

Covers:
  - github_sync._sync_comments  — create / update / delete / API failure
  - github_sync.sync            — conditional comment re-fetch on updated_at change
  - assist.context._github_context_lines — no external_id, body only, body+comments
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import github_sync


# ── In-memory DB ──────────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(hours=2)
LATER = NOW + timedelta(hours=1)


def _make_eng_item(external_id="github:owner/repo/issues/1", body=None, body_updated_at=None,
                    item_type="issue"):
    with TestSession() as db:
        item = models.EngineeringItem(
            external_id=external_id,
            title="Test issue",
            item_type=item_type,
            repo="owner/repo",
            number=1,
            url=f"https://github.com/owner/repo/{'pull' if item_type == 'pr' else 'issues'}/1",
            state="open",
            body=body,
            body_updated_at=body_updated_at,
            synced_at=NOW,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.id


def _gh_comment(comment_id, body, author="alice", created_at=None, updated_at=None):
    t = (created_at or NOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    u = (updated_at or NOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": comment_id, "body": body, "user": {"login": author},
            "created_at": t, "updated_at": u}


def _gh_review_comment(comment_id, body, author="coderabbitai[bot]", path="src/foo.py", line=42,
                        created_at=None, updated_at=None):
    t = (created_at or NOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    u = (updated_at or NOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": comment_id, "body": body, "user": {"login": author},
            "path": path, "line": line, "created_at": t, "updated_at": u}


# ── _sync_comments ─────────────────────────────────────────────────────────────

class TestSyncComments:

    def test_creates_new_comments(self):
        item_id = _make_eng_item()
        gh_comments = [
            _gh_comment(101, "First comment"),
            _gh_comment(102, "Second comment", author="bob"),
        ]
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=gh_comments):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestSession() as db:
            comments = db.query(models.EngineeringItemComment).filter_by(item_id=item_id).all()
            assert len(comments) == 2
            bodies = {c.body for c in comments}
            assert "First comment" in bodies
            assert "Second comment" in bodies

    def test_updates_existing_comment(self):
        item_id = _make_eng_item()
        with TestSession() as db:
            db.add(models.EngineeringItemComment(
                item_id=item_id, github_id=101, author="alice",
                body="Old body", created_at=NOW, updated_at=NOW,
            ))
            db.commit()

        updated = [_gh_comment(101, "Updated body", updated_at=LATER)]
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=updated):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestSession() as db:
            c = db.query(models.EngineeringItemComment).filter_by(github_id=101).first()
            assert c.body == "Updated body"

    def test_deletes_removed_comments(self):
        item_id = _make_eng_item()
        with TestSession() as db:
            db.add(models.EngineeringItemComment(
                item_id=item_id, github_id=201, author="alice",
                body="Will be removed", created_at=NOW, updated_at=NOW,
            ))
            db.add(models.EngineeringItemComment(
                item_id=item_id, github_id=202, author="bob",
                body="Will stay", created_at=NOW, updated_at=NOW,
            ))
            db.commit()

        # GitHub now only returns comment 202
        remaining = [_gh_comment(202, "Will stay")]
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=remaining):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestSession() as db:
            comments = db.query(models.EngineeringItemComment).filter_by(item_id=item_id).all()
            assert len(comments) == 1
            assert comments[0].github_id == 202

    def test_does_not_crash_on_api_failure(self):
        item_id = _make_eng_item()
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", side_effect=Exception("API down")):
                github_sync._sync_comments(db, item, token="fake")  # should not raise

    def test_preserves_author_on_update(self):
        item_id = _make_eng_item()
        with TestSession() as db:
            db.add(models.EngineeringItemComment(
                item_id=item_id, github_id=301, author="old_user",
                body="body", created_at=NOW, updated_at=NOW,
            ))
            db.commit()

        updated = [_gh_comment(301, "new body", author="new_user", updated_at=LATER)]
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=updated):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestSession() as db:
            c = db.query(models.EngineeringItemComment).filter_by(github_id=301).first()
            assert c.author == "new_user"

    def test_handles_null_user_field(self):
        """GitHub sometimes returns null for deleted accounts."""
        item_id = _make_eng_item()
        comment = {"id": 401, "body": "ghost comment", "user": None,
                   "created_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "updated_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=[comment]):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestSession() as db:
            c = db.query(models.EngineeringItemComment).filter_by(github_id=401).first()
            assert c is not None
            assert c.author is None


# ── PR review comments (CodeRabbit feedback integration) ─────────────────────

class TestPrReviewComments:

    def test_issue_never_fetches_review_comments(self):
        """Only PRs have inline review comments -- calling the endpoint for an issue
        number would 404, so _sync_comments must gate on item_type == 'pr'."""
        item_id = _make_eng_item(item_type="issue")
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=[]), \
                 patch("github_sync._fetch_review_comments") as mock_fetch_review:
                github_sync._sync_comments(db, item, token="fake")
            db.commit()
        mock_fetch_review.assert_not_called()

    def test_pr_fetches_review_comments(self):
        item_id = _make_eng_item(external_id="github:owner/repo/pull/1", item_type="pr")
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=[]), \
                 patch("github_sync._fetch_review_comments", return_value=[]) as mock_fetch_review:
                github_sync._sync_comments(db, item, token="fake")
            db.commit()
        mock_fetch_review.assert_called_once()

    def test_stores_coderabbit_review_comment_with_diff_position(self):
        item_id = _make_eng_item(external_id="github:owner/repo/pull/1", item_type="pr")
        review_comments = [_gh_review_comment(601, "Consider using a set here.")]
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=[]), \
                 patch("github_sync._fetch_review_comments", return_value=review_comments):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestSession() as db:
            c = db.query(models.EngineeringItemComment).filter_by(github_id=601).first()
            assert c is not None
            assert c.comment_type == "pr_review_comment"
            assert c.diff_path == "src/foo.py"
            assert c.diff_line == 42
            assert c.author == "coderabbitai[bot]"

    def test_filters_out_non_coderabbit_review_comments(self):
        item_id = _make_eng_item(external_id="github:owner/repo/pull/1", item_type="pr")
        review_comments = [
            _gh_review_comment(701, "Nice catch!", author="a-human-reviewer"),
            _gh_review_comment(702, "Real CodeRabbit feedback", author="coderabbitai[bot]"),
        ]
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=[]), \
                 patch("github_sync._fetch_review_comments", return_value=review_comments):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestSession() as db:
            comments = db.query(models.EngineeringItemComment).filter_by(item_id=item_id).all()
            assert len(comments) == 1
            assert comments[0].github_id == 702

    def test_falls_back_to_original_line_when_line_is_null(self):
        """A comment on an outdated diff position has line=None but original_line set."""
        item_id = _make_eng_item(external_id="github:owner/repo/pull/1", item_type="pr")
        comment = _gh_review_comment(801, "stale comment")
        comment["line"] = None
        comment["original_line"] = 17
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=[]), \
                 patch("github_sync._fetch_review_comments", return_value=[comment]):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestSession() as db:
            c = db.query(models.EngineeringItemComment).filter_by(github_id=801).first()
            assert c.diff_line == 17

    def test_deletes_removed_review_comments_without_touching_issue_comments(self):
        item_id = _make_eng_item(external_id="github:owner/repo/pull/1", item_type="pr")
        with TestSession() as db:
            db.add(models.EngineeringItemComment(
                item_id=item_id, github_id=901, comment_type="issue_comment",
                author="alice", body="conversation comment", created_at=NOW, updated_at=NOW,
            ))
            db.add(models.EngineeringItemComment(
                item_id=item_id, github_id=902, comment_type="pr_review_comment",
                author="coderabbitai[bot]", body="stale suggestion",
                diff_path="a.py", diff_line=1, created_at=NOW, updated_at=NOW,
            ))
            db.commit()

        # GitHub no longer returns comment 902; issue comment 901 isn't touched by this fetch.
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", side_effect=Exception("transient")), \
                 patch("github_sync._fetch_review_comments", return_value=[]):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestSession() as db:
            comments = db.query(models.EngineeringItemComment).filter_by(item_id=item_id).all()
            assert len(comments) == 1
            assert comments[0].github_id == 901, \
                "issue comment must survive a transient issue-comment fetch failure"

    def test_review_comment_fetch_failure_does_not_affect_issue_comments(self):
        item_id = _make_eng_item(external_id="github:owner/repo/pull/1", item_type="pr")
        issue_comments = [_gh_comment(1001, "a normal conversation comment")]
        with TestSession() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            with patch("github_sync._fetch_comments", return_value=issue_comments), \
                 patch("github_sync._fetch_review_comments", side_effect=Exception("API down")):
                github_sync._sync_comments(db, item, token="fake")  # should not raise
            db.commit()

        with TestSession() as db:
            comments = db.query(models.EngineeringItemComment).filter_by(item_id=item_id).all()
            assert len(comments) == 1
            assert comments[0].github_id == 1001


# ── Conditional comment sync in github_sync.sync ─────────────────────────────

class TestConditionalCommentSync:

    def _fake_github_item(self, updated_at=None):
        t = (updated_at or NOW).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "title": "Test issue",
            "html_url": "https://github.com/owner/repo/issues/1",
            "number": 1,
            "body": "Issue body",
            "updated_at": t,
        }

    def test_syncs_comments_for_new_item(self):
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            db.commit()

        with patch("github_sync._fetch_items", return_value=[self._fake_github_item()]), \
             patch("github_sync._fetch_comments", return_value=[]) as mock_fetch_comments, \
             patch("github_sync._fetch_project_statuses", return_value={}), \
             patch("embeddings.upsert_eng_bg", return_value=None):
            with TestSession() as db:
                github_sync.sync(db)

        mock_fetch_comments.assert_called_once()

    def test_skips_comment_sync_when_updated_at_unchanged(self):
        """If updated_at hasn't changed since last sync, skip comment re-fetch."""
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            # Pre-existing item with body_updated_at == NOW
            db.add(models.EngineeringItem(
                external_id="github:owner/repo/issues/1",
                title="Test issue", item_type="issue",
                repo="owner/repo", number=1,
                url="https://github.com/owner/repo/issues/1",
                state="open", synced_at=NOW, body_updated_at=NOW,
            ))
            db.commit()

        with patch("github_sync._fetch_items", return_value=[self._fake_github_item(updated_at=NOW)]), \
             patch("github_sync._fetch_comments", return_value=[]) as mock_fetch_comments, \
             patch("github_sync._fetch_project_statuses", return_value={}), \
             patch("embeddings.upsert_eng_bg", return_value=None):
            with TestSession() as db:
                github_sync.sync(db)

        mock_fetch_comments.assert_not_called()

    def test_syncs_comments_when_updated_at_advances(self):
        """If updated_at has advanced past body_updated_at, re-fetch comments."""
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            db.add(models.EngineeringItem(
                external_id="github:owner/repo/issues/1",
                title="Test issue", item_type="issue",
                repo="owner/repo", number=1,
                url="https://github.com/owner/repo/issues/1",
                state="open", synced_at=EARLIER, body_updated_at=EARLIER,
            ))
            db.commit()

        with patch("github_sync._fetch_items", return_value=[self._fake_github_item(updated_at=LATER)]), \
             patch("github_sync._fetch_comments", return_value=[]) as mock_fetch_comments, \
             patch("github_sync._fetch_project_statuses", return_value={}), \
             patch("embeddings.upsert_eng_bg", return_value=None):
            with TestSession() as db:
                github_sync.sync(db)

        mock_fetch_comments.assert_called_once()


# ── _github_context_lines ─────────────────────────────────────────────────────

class TestGithubContextLines:

    def _make_card(self, external_id=None):
        with TestSession() as db:
            card = models.Card(title="Test card", section="today", position=0,
                               external_id=external_id)
            db.add(card)
            db.commit()
            db.refresh(card)
            return card

    def test_returns_empty_when_no_external_id(self):
        from assist.context import _github_context_lines
        with TestSession() as db:
            card = self._make_card()
            card = db.query(models.Card).filter_by(id=card.id).first()
            result = _github_context_lines(db, card)
        assert result == []

    def test_returns_empty_when_no_matching_eng_item(self):
        from assist.context import _github_context_lines
        with TestSession() as db:
            card = self._make_card(external_id="github:owner/repo/issues/999")
            card = db.query(models.Card).filter_by(id=card.id).first()
            result = _github_context_lines(db, card)
        assert result == []

    def test_includes_issue_header(self):
        from assist.context import _github_context_lines
        _make_eng_item(body="The bug description")
        with TestSession() as db:
            card = models.Card(title="Fix bug", section="today", position=0,
                               external_id="github:owner/repo/issues/1")
            db.add(card)
            db.commit()
            card = db.query(models.Card).filter_by(title="Fix bug").first()
            result = _github_context_lines(db, card)

        full_text = "\n".join(result)
        assert "### GitHub Issue:" in full_text
        assert "owner/repo" in full_text

    def test_includes_body(self):
        from assist.context import _github_context_lines
        _make_eng_item(body="Steps to reproduce the bug")
        with TestSession() as db:
            card = models.Card(title="Fix bug", section="today", position=0,
                               external_id="github:owner/repo/issues/1")
            db.add(card)
            db.commit()
            card = db.query(models.Card).filter_by(title="Fix bug").first()
            result = _github_context_lines(db, card)

        assert "Steps to reproduce the bug" in "\n".join(result)

    def test_includes_comments(self):
        from assist.context import _github_context_lines
        item_id = _make_eng_item(body="Issue description")
        with TestSession() as db:
            db.add(models.EngineeringItemComment(
                item_id=item_id, github_id=501, author="charlie",
                body="Please add unit tests too.",
                created_at=NOW, updated_at=NOW,
            ))
            db.commit()

        with TestSession() as db:
            card = models.Card(title="Fix", section="today", position=0,
                               external_id="github:owner/repo/issues/1")
            db.add(card)
            db.commit()
            card = db.query(models.Card).filter_by(title="Fix").first()
            result = _github_context_lines(db, card)

        full_text = "\n".join(result)
        assert "charlie" in full_text
        assert "unit tests" in full_text

    def test_skips_empty_body(self):
        from assist.context import _github_context_lines
        _make_eng_item(body=None)
        with TestSession() as db:
            card = models.Card(title="Fix", section="today", position=0,
                               external_id="github:owner/repo/issues/1")
            db.add(card)
            db.commit()
            card = db.query(models.Card).filter_by(title="Fix").first()
            result = _github_context_lines(db, card)

        full_text = "\n".join(result)
        assert "**Description:**" not in full_text
