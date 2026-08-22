"""
Tests for POST /api/engineering/{item_id}/refresh.

Covers: missing item, missing token, and GitHub request failures (the fix
in this file -- a network/HTTP error used to propagate as an unhandled 500
instead of a clean error response).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import app_setting_keys as setting_keys
from main import app
from deps import get_db

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=test_engine)
    yield
    models.Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _set_token(token="ghp_test"):
    with TestingSessionLocal() as db:
        db.add(models.AppSetting(key=setting_keys.GITHUB_TOKEN, value=token))
        db.commit()


def _make_item(external_id="github:owner/repo/issues/42"):
    with TestingSessionLocal() as db:
        item = models.EngineeringItem(
            external_id=external_id, title="Old title", item_type="issue",
            repo="owner/repo", number=42, url="https://github.com/owner/repo/issues/42",
            state="open", synced_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.id


class TestRefreshEngineeringItem:

    def test_404_when_item_not_found(self, client):
        res = client.post("/api/engineering/9999/refresh")
        assert res.status_code == 404

    def test_400_when_no_token_configured(self, client):
        item_id = _make_item()
        res = client.post(f"/api/engineering/{item_id}/refresh")
        assert res.status_code == 400

    def test_502_on_github_connection_error(self, client):
        _set_token()
        item_id = _make_item()
        with patch("routers.engineering._requests.get", side_effect=requests.exceptions.ConnectionError("boom")):
            res = client.post(f"/api/engineering/{item_id}/refresh")
        assert res.status_code == 502
        assert "GitHub request failed" in res.json()["detail"]

    def test_502_on_github_http_error(self, client):
        _set_token()
        item_id = _make_item()

        class _FakeResponse:
            def raise_for_status(self):
                raise requests.exceptions.HTTPError("404 Client Error")

        with patch("routers.engineering._requests.get", return_value=_FakeResponse()):
            res = client.post(f"/api/engineering/{item_id}/refresh")
        assert res.status_code == 502

    def test_succeeds_and_updates_title(self, client):
        _set_token()
        item_id = _make_item()

        class _FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"title": "New title", "body": "Updated body", "updated_at": "2026-06-20T10:00:00Z"}

        with patch("routers.engineering._requests.get", return_value=_FakeResponse()), \
             patch("github_sync._sync_comments", return_value=None):
            res = client.post(f"/api/engineering/{item_id}/refresh")

        assert res.status_code == 200
        assert res.json()["title"] == "New title"


def _make_comment(item_id, github_id=1, comment_type="pr_review_comment", **overrides):
    with TestingSessionLocal() as db:
        comment = models.EngineeringItemComment(
            item_id=item_id, github_id=github_id, author="coderabbitai[bot]",
            body="Consider using a set here.", comment_type=comment_type,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            **overrides,
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment.id


class TestDismissComment:

    def test_dismisses_a_comment(self, client):
        item_id = _make_item()
        comment_id = _make_comment(item_id)

        res = client.patch(f"/api/engineering/comments/{comment_id}/dismiss", json={"dismissed": True})
        assert res.status_code == 200
        body = res.json()
        assert body["dismissed"] is True
        assert body["dismissed_at"] is not None

        with TestingSessionLocal() as db:
            c = db.query(models.EngineeringItemComment).filter_by(id=comment_id).first()
            assert c.dismissed is True
            assert c.dismissed_at is not None

    def test_undismisses_a_comment(self, client):
        item_id = _make_item()
        comment_id = _make_comment(item_id, dismissed=True, dismissed_at=datetime.now(timezone.utc))

        res = client.patch(f"/api/engineering/comments/{comment_id}/dismiss", json={"dismissed": False})
        assert res.status_code == 200
        body = res.json()
        assert body["dismissed"] is False
        assert body["dismissed_at"] is None

        with TestingSessionLocal() as db:
            c = db.query(models.EngineeringItemComment).filter_by(id=comment_id).first()
            assert c.dismissed is False
            assert c.dismissed_at is None

    def test_404_for_missing_comment(self, client):
        res = client.patch("/api/engineering/comments/999999/dismiss", json={"dismissed": True})
        assert res.status_code == 404

    def test_resync_does_not_undismiss(self, client):
        """github_sync.py's upsert must never touch dismissed/dismissed_at on an existing
        row -- re-fetching an already-dismissed comment (even with an edited body) must
        leave it dismissed."""
        import github_sync
        item_id = _make_item(external_id="github:owner/repo/pull/1")
        comment_id = _make_comment(item_id, github_id=55)

        client.patch(f"/api/engineering/comments/{comment_id}/dismiss", json={"dismissed": True})

        with TestingSessionLocal() as db:
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            item.item_type = "pr"
            db.commit()
            item = db.query(models.EngineeringItem).filter_by(id=item_id).first()
            edited = [{
                "id": 55, "body": "Edited on GitHub", "user": {"login": "coderabbitai[bot]"},
                "path": "a.py", "line": 1,
                "created_at": "2026-06-20T10:00:00Z", "updated_at": "2026-06-20T11:00:00Z",
            }]
            with patch("github_sync._fetch_comments", return_value=[]), \
                 patch("github_sync._fetch_review_comments", return_value=edited):
                github_sync._sync_comments(db, item, token="fake")
            db.commit()

        with TestingSessionLocal() as db:
            c = db.query(models.EngineeringItemComment).filter_by(id=comment_id).first()
            assert c.body == "Edited on GitHub"
            assert c.dismissed is True, "re-syncing must not silently undismiss a comment"
