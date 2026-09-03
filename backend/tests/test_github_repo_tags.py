"""
Tests for per-repo tag rules: config storage, matching, and application when
cards are created from GitHub items.

Covers:
  - github_sync.get_repo_tags_config / save_repo_tags_config
  - github_sync._tags_for_repo         — owner-only, repo-only, union, no match
  - github_sync.tag_ids_for_external_id
  - github_sync.sync                   — auto-created card picks up repo tags
  - routers.cards.create_card          — manual "Add to board" picks up repo tags,
                                          and dedupes by external_id (no duplicate
                                          cards from re-adding an already-linked item)
  - routers.engineering.get_engineering_items — items list shows computed repo tags,
    ordered by repo / status-lane / title
  - GET/PUT /api/engineering/repo-tags — config CRUD endpoints
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import github_sync
from main import app
from deps import get_db


# ── In-memory DB ──────────────────────────────────────────────────────────────

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=engine)
    yield
    models.Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_tag(name, color="#fff"):
    with TestSession() as db:
        tag = models.Tag(name=name, color=color)
        db.add(tag)
        db.commit()
        db.refresh(tag)
        return tag.id


# ── _tags_for_repo ──────────────────────────────────────────────────────────

class TestTagsForRepo:

    def test_no_match_returns_empty(self):
        assert github_sync._tags_for_repo({}, "owner/repo") == []

    def test_matches_exact_repo(self):
        config = {"owner/repo": [1, 2]}
        assert github_sync._tags_for_repo(config, "owner/repo") == [1, 2]

    def test_matches_owner_only(self):
        config = {"owner": [3]}
        assert github_sync._tags_for_repo(config, "owner/repo") == [3]
        assert github_sync._tags_for_repo(config, "owner/other-repo") == [3]

    def test_unions_owner_and_repo_rules(self):
        config = {"owner": [1], "owner/repo": [2]}
        assert github_sync._tags_for_repo(config, "owner/repo") == [1, 2]

    def test_dedupes_overlapping_ids(self):
        config = {"owner": [1, 2], "owner/repo": [2, 3]}
        assert github_sync._tags_for_repo(config, "owner/repo") == [1, 2, 3]

    def test_unrelated_owner_does_not_match(self):
        config = {"someone-else": [1]}
        assert github_sync._tags_for_repo(config, "owner/repo") == []

    def test_owner_rule_matches_case_insensitively(self):
        """GitHub owner/repo names are case-preserving but case-insensitive —
        a rule saved as lowercase must still match the API's canonical casing."""
        config = {"trainsit": [2]}
        assert github_sync._tags_for_repo(config, "Trainsit/trainsit") == [2]

    def test_owner_rule_matches_mixed_case_repo(self):
        config = {"mrericsir": [1]}
        assert github_sync._tags_for_repo(config, "MrEricSir/Modipulate") == [1]

    def test_exact_repo_rule_matches_case_insensitively(self):
        config = {"owner/repo": [5]}
        assert github_sync._tags_for_repo(config, "Owner/Repo") == [5]

    def test_case_variant_rules_are_unioned(self):
        """If both a lowercase and differently-cased rule exist for the same
        owner, both should contribute tags rather than one silently winning."""
        config = {"trainsit": [1], "Trainsit": [2]}
        assert github_sync._tags_for_repo(config, "Trainsit/trainsit") == [1, 2]


class TestGetRepoTagsConfig:

    def test_round_trips_through_app_setting(self):
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {"owner/repo": [1, 2]})
        with TestSession() as db:
            assert github_sync.get_repo_tags_config(db) == {"owner/repo": [1, 2]}

    def test_defaults_to_empty_dict(self):
        with TestSession() as db:
            assert github_sync.get_repo_tags_config(db) == {}


class TestTagIdsForExternalId:

    def test_none_external_id_returns_empty(self):
        with TestSession() as db:
            assert github_sync.tag_ids_for_external_id(db, None) == []

    def test_non_github_external_id_returns_empty(self):
        with TestSession() as db:
            assert github_sync.tag_ids_for_external_id(db, "not-a-github-id") == []

    def test_resolves_repo_tags_from_external_id(self):
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {"owner/repo": [7]})
        with TestSession() as db:
            result = github_sync.tag_ids_for_external_id(db, "github:owner/repo/issues/42")
        assert result == [7]


# ── sync() auto-creates a tagged card on In Progress transition ─────────────

class TestSyncAppliesRepoTags:

    def _fake_github_item(self):
        return {
            "title": "Test issue",
            "html_url": "https://github.com/owner/repo/issues/1",
            "number": 1,
            "body": "Issue body",
            "updated_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _project_status_for(self, item_id, status="In Progress"):
        return {item_id: {"project_name": "Board", "project_status": status}}

    def test_auto_created_card_gets_repo_tags(self):
        tag_id = _make_tag("MyApp")
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            github_sync.save_repo_tags_config(db, {"owner/repo": [tag_id]})
            db.commit()

        with patch("github_sync._fetch_items", return_value=[self._fake_github_item()]), \
             patch("github_sync._fetch_comments", return_value=[]), \
             patch("embeddings.upsert_eng_bg", return_value=None):
            with TestSession() as db:
                # First sync: create the EngineeringItem (not yet "In Progress")
                with patch("github_sync._fetch_project_statuses", return_value={}):
                    github_sync.sync(db)

            with TestSession() as db:
                item = db.query(models.EngineeringItem).filter_by(
                    external_id="github:owner/repo/issues/1"
                ).first()
                item_id = item.id

            with patch("github_sync._fetch_project_statuses",
                        return_value=self._project_status_for(item_id)):
                with TestSession() as db:
                    github_sync.sync(db)

        with TestSession() as db:
            card = db.query(models.Card).filter_by(
                external_id="github:owner/repo/issues/1"
            ).first()
            assert card is not None
            tag_names = {t.name for t in card.tags}
            assert tag_names == {"MyApp"}

    def test_owner_level_rule_applies_to_any_repo(self):
        tag_id = _make_tag("Personal")
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            github_sync.save_repo_tags_config(db, {"owner": [tag_id]})
            db.commit()

        with patch("github_sync._fetch_items", return_value=[self._fake_github_item()]), \
             patch("github_sync._fetch_comments", return_value=[]), \
             patch("embeddings.upsert_eng_bg", return_value=None):
            with TestSession() as db:
                with patch("github_sync._fetch_project_statuses", return_value={}):
                    github_sync.sync(db)

            with TestSession() as db:
                item_id = db.query(models.EngineeringItem).filter_by(
                    external_id="github:owner/repo/issues/1"
                ).first().id

            with patch("github_sync._fetch_project_statuses",
                        return_value=self._project_status_for(item_id)):
                with TestSession() as db:
                    github_sync.sync(db)

        with TestSession() as db:
            card = db.query(models.Card).filter_by(
                external_id="github:owner/repo/issues/1"
            ).first()
            assert {t.name for t in card.tags} == {"Personal"}

    def test_no_matching_rule_leaves_card_untagged(self):
        with TestSession() as db:
            db.add(models.AppSetting(key="github_token", value="fake_token"))
            db.commit()

        with patch("github_sync._fetch_items", return_value=[self._fake_github_item()]), \
             patch("github_sync._fetch_comments", return_value=[]), \
             patch("embeddings.upsert_eng_bg", return_value=None):
            with TestSession() as db:
                with patch("github_sync._fetch_project_statuses", return_value={}):
                    github_sync.sync(db)

            with TestSession() as db:
                item_id = db.query(models.EngineeringItem).filter_by(
                    external_id="github:owner/repo/issues/1"
                ).first().id

            with patch("github_sync._fetch_project_statuses",
                        return_value=self._project_status_for(item_id)):
                with TestSession() as db:
                    github_sync.sync(db)

        with TestSession() as db:
            card = db.query(models.Card).filter_by(
                external_id="github:owner/repo/issues/1"
            ).first()
            assert card.tags == []


# ── POST /api/cards (manual "Add to board") applies repo tags ───────────────

class TestCreateCardAppliesRepoTags:

    def test_manual_card_creation_picks_up_repo_tags(self, client):
        tag_id = _make_tag("MyApp")
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {"owner/repo": [tag_id]})

        resp = client.post("/api/cards", json={
            "title": "GitHub Issue: Fix bug",
            "section": "today",
            "external_id": "github:owner/repo/issues/1",
            "tag_ids": [],
        })
        assert resp.status_code == 201
        tag_names = {t["name"] for t in resp.json()["tags"]}
        assert tag_names == {"MyApp"}

    def test_explicit_tags_are_preserved_alongside_repo_tags(self, client):
        repo_tag_id = _make_tag("MyApp")
        manual_tag_id = _make_tag("Urgent")
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {"owner/repo": [repo_tag_id]})

        resp = client.post("/api/cards", json={
            "title": "GitHub Issue: Fix bug",
            "section": "today",
            "external_id": "github:owner/repo/issues/1",
            "tag_ids": [manual_tag_id],
        })
        assert resp.status_code == 201
        tag_names = {t["name"] for t in resp.json()["tags"]}
        assert tag_names == {"MyApp", "Urgent"}

    def test_card_without_external_id_unaffected(self, client):
        _make_tag("MyApp")
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {"owner/repo": [1]})

        resp = client.post("/api/cards", json={
            "title": "Plain task",
            "section": "today",
            "tag_ids": [],
        })
        assert resp.status_code == 201
        assert resp.json()["tags"] == []


# ── POST /api/cards dedupes by external_id (redundant-ticket fix) ───────────
#
# User report: the Engineering page sometimes created duplicate cards for the same
# GitHub issue/PR. Root cause: github_sync.py's automatic sync-triggered card creation
# already checked "does a non-archived card with this external_id exist" before
# creating one, but routers.cards.create_card_row (used by manual "Add to board" and
# Telegram capture) had no such check -- clicking "Add to board" for an item that
# already had a linked card (e.g. auto-created earlier, or added once before) created
# a second, genuinely duplicate card.

class TestCreateCardDedupesByExternalId:

    def test_second_add_to_board_returns_the_existing_card_not_a_duplicate(self, client):
        first = client.post("/api/cards", json={
            "title": "Fix login bug", "section": "today",
            "external_id": "github:owner/repo/issues/1", "tag_ids": [],
        })
        assert first.status_code == 201
        first_id = first.json()["id"]

        second = client.post("/api/cards", json={
            "title": "Fix login bug", "section": "week",
            "external_id": "github:owner/repo/issues/1", "tag_ids": [],
        })
        assert second.status_code == 201
        assert second.json()["id"] == first_id
        # The existing card is returned as-is -- the second call's section is ignored,
        # not applied as an update.
        assert second.json()["section"] == "today"

        with TestSession() as db:
            count = db.query(models.Card).filter_by(
                external_id="github:owner/repo/issues/1"
            ).count()
        assert count == 1

    def test_re_adding_after_the_original_was_archived_creates_a_fresh_card(self, client):
        """Mirrors github_sync.py's own archived=False dedup check -- archiving a card
        means "done with this," so if the same external item comes up again it's
        treated as new, not blocked forever."""
        first = client.post("/api/cards", json={
            "title": "Fix login bug", "section": "today",
            "external_id": "github:owner/repo/issues/1", "tag_ids": [],
        })
        first_id = first.json()["id"]
        with TestSession() as db:
            card = db.query(models.Card).filter_by(id=first_id).first()
            card.archived = True
            db.commit()

        second = client.post("/api/cards", json={
            "title": "Fix login bug", "section": "today",
            "external_id": "github:owner/repo/issues/1", "tag_ids": [],
        })
        assert second.status_code == 201
        assert second.json()["id"] != first_id

        with TestSession() as db:
            count = db.query(models.Card).filter_by(
                external_id="github:owner/repo/issues/1"
            ).count()
        assert count == 2

    def test_different_external_ids_both_get_created(self, client):
        r1 = client.post("/api/cards", json={
            "title": "Fix login bug", "section": "today",
            "external_id": "github:owner/repo/issues/1", "tag_ids": [],
        })
        r2 = client.post("/api/cards", json={
            "title": "Bump deps", "section": "today",
            "external_id": "github:owner/repo/pull/2", "tag_ids": [],
        })
        assert r1.json()["id"] != r2.json()["id"]


# ── GET /api/engineering/items shows computed repo tags ─────────────────────

def _make_eng_item(repo="owner/repo", external_id="github:owner/repo/issues/1", title="Test issue",
                    number=1, project_status=None, item_type="issue"):
    with TestSession() as db:
        item = models.EngineeringItem(
            external_id=external_id,
            title=title,
            item_type=item_type,
            repo=repo,
            number=number,
            url=f"https://github.com/{repo}/issues/{number}",
            state="open",
            project_status=project_status,
            synced_at=NOW,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.id


class TestEngineeringItemsIncludesTags:

    def test_item_includes_matching_repo_tags(self, client):
        tag_id = _make_tag("MyApp")
        _make_eng_item(repo="owner/repo")
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {"owner/repo": [tag_id]})

        resp = client.get("/api/engineering/items")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert {t["name"] for t in items[0]["tags"]} == {"MyApp"}

    def test_item_includes_owner_level_tags(self, client):
        tag_id = _make_tag("Personal")
        _make_eng_item(repo="owner/repo")
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {"owner": [tag_id]})

        resp = client.get("/api/engineering/items")
        items = resp.json()
        assert {t["name"] for t in items[0]["tags"]} == {"Personal"}

    def test_item_without_matching_rule_has_no_tags(self, client):
        _make_eng_item(repo="owner/repo")
        resp = client.get("/api/engineering/items")
        items = resp.json()
        assert items[0]["tags"] == []

    def test_different_repos_get_different_tags(self, client):
        my_app_id = _make_tag("MyApp")
        other_id = _make_tag("Other")
        _make_eng_item(repo="owner/repo", external_id="github:owner/repo/issues/1", title="In MyApp")
        _make_eng_item(repo="owner/other-repo", external_id="github:owner/other-repo/issues/2", title="In Other")
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {
                "owner/repo": [my_app_id],
                "owner/other-repo": [other_id],
            })

        resp = client.get("/api/engineering/items")
        items = {item["title"]: item for item in resp.json()}
        assert {t["name"] for t in items["In MyApp"]["tags"]} == {"MyApp"}
        assert {t["name"] for t in items["In Other"]["tags"]} == {"Other"}


# ── GET /api/engineering/items ordering: repo, then status/lane, then title ─

class TestEngineeringItemsOrdering:

    def test_grouped_by_repo_before_anything_else(self, client):
        _make_eng_item(repo="zzz-repo", external_id="github:zzz-repo/issues/1", title="Z item", number=1)
        _make_eng_item(repo="aaa-repo", external_id="github:aaa-repo/issues/1", title="A item", number=1)

        resp = client.get("/api/engineering/items")
        titles = [item["title"] for item in resp.json()]
        assert titles == ["A item", "Z item"]

    def test_repo_grouping_is_case_insensitive(self, client):
        """Repo names differ only by GitHub's canonical casing — must still cluster together."""
        _make_eng_item(repo="Owner/repo", external_id="github:Owner/repo/issues/1", title="First", number=1)
        _make_eng_item(repo="other/repo", external_id="github:other/repo/issues/1", title="Second", number=1)
        _make_eng_item(repo="Owner/repo", external_id="github:Owner/repo/issues/2", title="Third", number=2)

        resp = client.get("/api/engineering/items")
        titles = [item["title"] for item in resp.json()]
        # Both Owner/repo items are adjacent, not split apart by the "other/repo" item
        assert titles.index("Third") - titles.index("First") == 1

    def test_within_repo_grouped_by_status_then_title(self, client):
        _make_eng_item(repo="owner/repo", external_id="github:owner/repo/issues/1", title="B backlog",
                        number=1, project_status="Backlog")
        _make_eng_item(repo="owner/repo", external_id="github:owner/repo/issues/2", title="A backlog",
                        number=2, project_status="Backlog")
        _make_eng_item(repo="owner/repo", external_id="github:owner/repo/issues/3", title="A in progress",
                        number=3, project_status="In Progress")

        resp = client.get("/api/engineering/items")
        titles = [item["title"] for item in resp.json()]
        # "Backlog" sorts before "In Progress" alphabetically; within each, title breaks ties
        assert titles == ["A backlog", "B backlog", "A in progress"]

    def test_items_without_status_sort_after_items_with_status_in_same_repo(self, client):
        _make_eng_item(repo="owner/repo", external_id="github:owner/repo/issues/1", title="No status",
                        number=1, project_status=None)
        _make_eng_item(repo="owner/repo", external_id="github:owner/repo/issues/2", title="Has status",
                        number=2, project_status="Todo")

        resp = client.get("/api/engineering/items")
        titles = [item["title"] for item in resp.json()]
        assert titles == ["Has status", "No status"]


# ── GET/PUT /api/engineering/repo-tags ───────────────────────────────────────

class TestRepoTagsEndpoint:

    def test_get_returns_empty_dict_when_unset(self, client):
        resp = client.get("/api/engineering/repo-tags")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_get_returns_saved_config(self, client):
        tag_id = _make_tag("MyApp")
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {"owner/repo": [tag_id]})

        resp = client.get("/api/engineering/repo-tags")
        assert resp.status_code == 200
        assert resp.json() == {"owner/repo": [tag_id]}

    def test_put_persists_config(self, client):
        tag_id = _make_tag("MyApp")
        resp = client.put("/api/engineering/repo-tags", json={"owner/repo": [tag_id]})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        with TestSession() as db:
            assert github_sync.get_repo_tags_config(db) == {"owner/repo": [tag_id]}

    def test_put_replaces_previous_config(self, client):
        old_tag = _make_tag("Old")
        new_tag = _make_tag("New")
        with TestSession() as db:
            github_sync.save_repo_tags_config(db, {"owner/old-repo": [old_tag]})

        resp = client.put("/api/engineering/repo-tags", json={"owner/new-repo": [new_tag]})
        assert resp.status_code == 200

        with TestSession() as db:
            assert github_sync.get_repo_tags_config(db) == {"owner/new-repo": [new_tag]}

    def test_saved_config_immediately_affects_items_list(self, client):
        """The repo-tags config isn't cached — a PUT should be visible on the
        very next GET /api/engineering/items with no server restart needed."""
        tag_id = _make_tag("MyApp")
        _make_eng_item(repo="owner/repo")

        before = client.get("/api/engineering/items").json()
        assert before[0]["tags"] == []

        client.put("/api/engineering/repo-tags", json={"owner/repo": [tag_id]})

        after = client.get("/api/engineering/items").json()
        assert {t["name"] for t in after[0]["tags"]} == {"MyApp"}
