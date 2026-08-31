"""
Tests for the bridge job queue endpoints (bridge/router.py + bridge/jobs.py).

Covers:
  - POST /api/bridge/jobs              — queue job (no spec → 400, valid → 200)
  - POST /api/bridge/jobs/queue-by-tag — queue every eligible tagged card
  - GET  /api/bridge/jobs/{id}         — get status
  - GET  /api/bridge/jobs/next/pending — atomic claim, lazy prompt build, double-claim,
                                         ?repos= filtering
  - POST /api/bridge/jobs/{id}/start   — record branch + agent name
  - POST /api/bridge/jobs/{id}/output  — stdout chunking + line-cap truncation
  - POST /api/bridge/jobs/{id}/complete
  - POST /api/bridge/jobs/{id}/error
  - GET  /api/bridge/jobs/card/{id}/latest

Served-script tests (install.py/agent.py content) live in test_bridge_scripts.py.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_card(title="Test card", spec=None, external_id=None, description=None,
                completed=False, archived=False, tags=()):
    with TestSession() as db:
        card = models.Card(
            title=title, section="today", position=0,
            spec=spec, external_id=external_id, description=description,
            completed=completed, archived=archived,
        )
        if tags:
            card.tags = db.query(models.Tag).filter(models.Tag.name.in_(tags)).all()
        db.add(card)
        db.commit()
        db.refresh(card)
        return card.id


def _make_tag(name, color="#6b7280"):
    with TestSession() as db:
        tag = models.Tag(name=name, color=color)
        db.add(tag)
        db.commit()
        db.refresh(tag)
        return tag.id


def _make_eng_item(external_id, title="Issue", body="Issue body", number=1, repo="owner/repo"):
    with TestSession() as db:
        item = models.EngineeringItem(
            external_id=external_id,
            title=title,
            item_type="issue",
            repo=repo,
            number=number,
            url=f"https://github.com/{repo}/issues/{number}",
            state="open",
            body=body,
            synced_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.id


def _make_comment(item_id, github_id, author="coderabbitai[bot]", body="Consider a set here.",
                   comment_type="pr_review_comment", diff_path="src/a.py", diff_line=10):
    with TestSession() as db:
        comment = models.EngineeringItemComment(
            item_id=item_id, github_id=github_id, author=author, body=body,
            comment_type=comment_type, diff_path=diff_path, diff_line=diff_line,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment.id


# ── validate_branch_name ──────────────────────────────────────────────────────

class TestValidateBranchName:

    def test_valid_name_returns_none(self):
        from bridge.jobs import validate_branch_name
        assert validate_branch_name("qtask/7-fix-thing") is None

    def test_empty_is_rejected(self):
        from bridge.jobs import validate_branch_name
        assert validate_branch_name("") is not None

    def test_whitespace_is_rejected(self):
        from bridge.jobs import validate_branch_name
        assert validate_branch_name("has space") is not None

    def test_leading_dash_is_rejected(self):
        from bridge.jobs import validate_branch_name
        assert validate_branch_name("-flag-like") is not None

    def test_dotdot_is_rejected(self):
        """The finding this guards against: a branch name string used to build a
        filesystem worktree path (WORKTREES_ROOT/repo_slug/branch) before git's own
        ref-name rules would ever reject it."""
        from bridge.jobs import validate_branch_name
        assert validate_branch_name("../../etc/passwd") is not None
        assert validate_branch_name("foo/../bar") is not None

    def test_control_characters_are_rejected(self):
        from bridge.jobs import validate_branch_name
        assert validate_branch_name("bad\x00name") is not None

    def test_git_special_characters_are_rejected(self):
        from bridge.jobs import validate_branch_name
        for bad in ["a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b"]:
            assert validate_branch_name(bad) is not None, f"expected rejection for {bad!r}"

    def test_leading_or_trailing_slash_is_rejected(self):
        from bridge.jobs import validate_branch_name
        assert validate_branch_name("/leading") is not None
        assert validate_branch_name("trailing/") is not None

    def test_internal_slash_is_allowed(self):
        """qtask/<id>-<slug> and custom namespaced names like feature/foo are normal."""
        from bridge.jobs import validate_branch_name
        assert validate_branch_name("qtask/12-my-feature") is None


# ── POST /api/bridge/jobs ─────────────────────────────────────────────────────

class TestCreateBridgeJob:

    def test_404_when_card_not_found(self, client):
        res = client.post("/api/bridge/jobs", json={"card_id": 9999})
        assert res.status_code == 404

    def test_400_when_card_has_no_spec(self, client):
        card_id = _make_card("No spec card", spec=None)
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 400
        assert "spec" in res.json()["detail"].lower()

    def test_creates_pending_job_with_spec(self, client):
        card_id = _make_card("My feature", spec="## Problem\nFix the thing")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "pending"
        assert data["card_id"] == card_id
        assert data["id"] is not None

    def test_prompt_snapshot_includes_spec(self, client):
        card_id = _make_card("Auth feature", spec="## Problem\nAdd OAuth")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 200
        with TestSession() as db:
            job = db.query(models.BridgeJob).filter_by(id=res.json()["id"]).first()
            assert job.prompt_snapshot is not None
            assert "Auth feature" in job.prompt_snapshot
            assert "Add OAuth" in job.prompt_snapshot

    def test_prompt_snapshot_includes_github_context(self, client):
        ext_id = "github:owner/repo/issues/1"
        card_id = _make_card("GH feature", spec="## Fix\nDo thing", external_id=ext_id)
        _make_eng_item(ext_id, body="Issue body text")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 200
        with TestSession() as db:
            job = db.query(models.BridgeJob).filter_by(id=res.json()["id"]).first()
            assert "Issue body text" in (job.prompt_snapshot or "")

    def test_result_field_is_none_initially(self, client):
        card_id = _make_card(spec="spec text")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.json()["result"] is None

    def test_target_repo_derived_from_github_external_id(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/myapp/issues/42")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.status_code == 200
        assert res.json()["target_repo"] == "owner/myapp"

    def test_target_repo_is_null_for_card_without_external_id(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.json()["target_repo"] is None

    def test_target_repo_is_null_for_non_github_external_id(self, client):
        card_id = _make_card(spec="s", external_id="jira:PROJ-123")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.json()["target_repo"] is None

    def test_requested_branch_name_is_stored_and_returned(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "branch_name": "my-custom-branch"})
        assert res.status_code == 200
        assert res.json()["requested_branch_name"] == "my-custom-branch"

    def test_requested_branch_name_is_null_when_not_given(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.json()["requested_branch_name"] is None

    def test_requested_branch_name_is_whitespace_trimmed(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "branch_name": "  my-branch  "})
        assert res.json()["requested_branch_name"] == "my-branch"

    def test_400_for_branch_name_containing_whitespace(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "branch_name": "has a space"})
        assert res.status_code == 400

    def test_400_for_branch_name_starting_with_a_dash(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "branch_name": "-not-a-flag"})
        assert res.status_code == 400

    def test_400_for_branch_name_containing_dotdot(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "branch_name": "../../etc/passwd"})
        assert res.status_code == 400

    def test_400_for_branch_name_with_control_characters(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "branch_name": "bad\x00name"})
        assert res.status_code == 400

    def test_400_for_branch_name_with_git_special_characters(self, client):
        card_id = _make_card(spec="s")
        for bad in ["a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b"]:
            res = client.post("/api/bridge/jobs", json={"card_id": card_id, "branch_name": bad})
            assert res.status_code == 400, f"expected 400 for branch_name={bad!r}"

    def test_400_for_branch_name_starting_or_ending_with_slash(self, client):
        card_id = _make_card(spec="s")
        for bad in ["/leading", "trailing/"]:
            res = client.post("/api/bridge/jobs", json={"card_id": card_id, "branch_name": bad})
            assert res.status_code == 400, f"expected 400 for branch_name={bad!r}"

    def test_requested_branch_name_appears_in_next_pending_payload(self, client):
        card_id = _make_card(spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id, "branch_name": "custom/name"})
        pending = client.get("/api/bridge/jobs/next/pending").json()["job"]
        assert pending["requested_branch_name"] == "custom/name"

    def test_depends_on_job_id_is_null_when_not_given(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.json()["depends_on_job_id"] is None

    def test_target_repo_override_is_respected(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/api-repo/issues/1")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "target_repo": "owner/web-repo"})
        assert res.status_code == 200
        assert res.json()["target_repo"] == "owner/web-repo"

    def test_target_repo_override_is_whitespace_trimmed(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "target_repo": "  owner/web-repo  "})
        assert res.json()["target_repo"] == "owner/web-repo"

    def test_no_target_repo_override_falls_back_to_derived_repo(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/api-repo/issues/1")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        assert res.json()["target_repo"] == "owner/api-repo"


# ── Cross-repo companion jobs (depends_on_job_id / target_repo) ────────────────
# See BRIDGE_CROSS_REPO_JOBS.md Phase 1.

class TestCrossRepoCompanionJob:

    def test_depends_on_job_id_starts_the_job_as_blocked(self, client):
        card_id = _make_card(spec="s")
        upstream = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()

        res = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/web-repo", "depends_on_job_id": upstream["id"],
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "blocked"
        assert data["depends_on_job_id"] == upstream["id"]
        assert data["target_repo"] == "owner/web-repo"

    def test_blocked_job_is_not_returned_by_next_pending(self, client):
        card_id = _make_card(spec="s")
        upstream = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()
        client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/web-repo", "depends_on_job_id": upstream["id"],
        })

        # Claim the upstream job -- the companion job must still not be claimable.
        first_claim = client.get("/api/bridge/jobs/next/pending").json()["job"]
        assert first_claim["id"] == upstream["id"]

        second_claim = client.get("/api/bridge/jobs/next/pending").json()["job"]
        assert second_claim is None

    def test_404_when_depends_on_job_id_does_not_exist(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "depends_on_job_id": 9999})
        assert res.status_code == 404

    def test_400_when_depends_on_job_id_belongs_to_a_different_card(self, client):
        card_a = _make_card(spec="s")
        card_b = _make_card(spec="s")
        job_on_b = client.post("/api/bridge/jobs", json={"card_id": card_b}).json()

        res = client.post("/api/bridge/jobs", json={
            "card_id": card_a, "depends_on_job_id": job_on_b["id"],
        })
        assert res.status_code == 400
        assert "same card" in res.json()["detail"].lower()

    def test_409_when_upstream_already_has_an_active_companion(self, client):
        card_id = _make_card(spec="s")
        upstream = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()
        client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/web-repo", "depends_on_job_id": upstream["id"],
        })

        res = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/other-repo", "depends_on_job_id": upstream["id"],
        })
        assert res.status_code == 409

    def test_a_new_companion_is_allowed_once_the_old_one_reaches_a_terminal_state(self, client):
        card_id = _make_card(spec="s")
        upstream = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()
        first_companion = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/web-repo", "depends_on_job_id": upstream["id"],
        }).json()
        with TestSession() as db:
            job = db.query(models.BridgeJob).filter_by(id=first_companion["id"]).first()
            job.status = "error"
            db.commit()

        res = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/web-repo", "depends_on_job_id": upstream["id"],
        })
        assert res.status_code == 200

    def test_depends_on_job_id_pointing_at_an_already_done_job_still_starts_blocked(self, client):
        """Creating a companion job against an upstream that's already finished is a valid,
        not-erroneous ordering -- it just means the Phase 2 unblock tick will pick it up on
        its very next run instead of waiting. Not decided here whether that tick has landed
        yet, only that job creation itself doesn't need to special-case it."""
        card_id = _make_card(spec="s")
        upstream = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()
        client.get("/api/bridge/jobs/next/pending")  # claim -> running
        client.post(f"/api/bridge/jobs/{upstream['id']}/complete", json={"result": "done"})

        res = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "depends_on_job_id": upstream["id"],
        })
        assert res.json()["status"] == "blocked"

    def test_companion_job_prompt_is_framed_with_its_target_repo(self, client):
        card_id = _make_card(spec="## Spec\nAdd export endpoint and wire up the UI")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id, "target_repo": "owner/web-repo"})
        assert res.status_code == 200
        with TestSession() as db:
            job = db.query(models.BridgeJob).filter_by(id=res.json()["id"]).first()
            assert "owner/web-repo" in job.prompt_snapshot
            assert "only implement the part that belongs" in job.prompt_snapshot
            assert "Add export endpoint" in job.prompt_snapshot

    def test_job_without_target_repo_override_has_no_framing_note(self, client):
        card_id = _make_card(spec="## Spec\ndo it")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        with TestSession() as db:
            job = db.query(models.BridgeJob).filter_by(id=res.json()["id"]).first()
            assert "only implement the part that belongs" not in job.prompt_snapshot


# ── GET /api/bridge/jobs/card/{id}/chain ────────────────────────────────────────

class TestJobChainEndpoint:

    def test_no_jobs_returns_both_null(self, client):
        card_id = _make_card(spec="s")
        res = client.get(f"/api/bridge/jobs/card/{card_id}/chain")
        assert res.status_code == 200
        assert res.json() == {"root": None, "companion": None}

    def test_single_job_is_root_with_no_companion(self, client):
        card_id = _make_card(spec="s")
        job = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()

        res = client.get(f"/api/bridge/jobs/card/{card_id}/chain").json()
        assert res["root"]["id"] == job["id"]
        assert res["companion"] is None

    def test_companion_job_is_paired_with_its_root(self, client):
        card_id = _make_card(spec="s")
        root = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()
        companion = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/web-repo", "depends_on_job_id": root["id"],
        }).json()

        res = client.get(f"/api/bridge/jobs/card/{card_id}/chain").json()
        assert res["root"]["id"] == root["id"]
        assert res["companion"]["id"] == companion["id"]

    def test_only_the_newest_root_is_returned_when_a_card_has_job_history(self, client):
        card_id = _make_card(spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id})
        newest = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()

        res = client.get(f"/api/bridge/jobs/card/{card_id}/chain").json()
        assert res["root"]["id"] == newest["id"]

    def test_unrelated_cards_dont_bleed_into_each_others_chain(self, client):
        card_a = _make_card(spec="s")
        card_b = _make_card(spec="s")
        job_a = client.post("/api/bridge/jobs", json={"card_id": card_a}).json()
        client.post("/api/bridge/jobs", json={"card_id": card_b})

        res = client.get(f"/api/bridge/jobs/card/{card_a}/chain").json()
        assert res["root"]["id"] == job_a["id"]
        assert res["companion"] is None


# ── GET /api/bridge/jobs/status ───────────────────────────────────────────────

class TestBridgeJobStatusesEndpoint:

    def test_no_jobs_returns_empty(self, client):
        res = client.get("/api/bridge/jobs/status")
        assert res.status_code == 200
        assert res.json() == {"statuses": {}}

    def test_card_with_no_job_is_absent_from_the_map(self, client):
        _make_card(spec="s")
        res = client.get("/api/bridge/jobs/status").json()
        assert res["statuses"] == {}

    def test_single_pending_job_reports_pending(self, client):
        card_id = _make_card(spec="s")
        job = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()

        res = client.get("/api/bridge/jobs/status").json()
        assert res["statuses"][str(card_id)] == {"job_id": job["id"], "status": "pending"}

    def test_reports_running_after_being_claimed(self, client):
        card_id = _make_card(spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id})
        client.get("/api/bridge/jobs/next/pending")  # claims it, sets running

        res = client.get("/api/bridge/jobs/status").json()
        assert res["statuses"][str(card_id)]["status"] == "running"

    def test_reports_done_after_completion(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "PR opened"})

        res = client.get("/api/bridge/jobs/status").json()
        assert res["statuses"][str(card_id)]["status"] == "done"

    def test_reports_error(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/error", json={"result": "boom"})

        res = client.get("/api/bridge/jobs/status").json()
        assert res["statuses"][str(card_id)]["status"] == "error"

    def test_only_the_newest_root_job_counts(self, client):
        """A card whose first attempt errored but was later re-run from scratch shouldn't
        show a stale error forever."""
        card_id = _make_card(spec="s")
        old_job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{old_job_id}/error", json={"result": "boom"})
        new_job = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()

        res = client.get("/api/bridge/jobs/status").json()
        assert res["statuses"][str(card_id)] == {"job_id": new_job["id"], "status": "pending"}

    def test_multiple_cards_each_get_their_own_status(self, client):
        card_a = _make_card(spec="s")
        card_b = _make_card(spec="s")
        job_a = client.post("/api/bridge/jobs", json={"card_id": card_a}).json()
        job_b_id = client.post("/api/bridge/jobs", json={"card_id": card_b}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_b_id}/error", json={"result": "boom"})

        res = client.get("/api/bridge/jobs/status").json()
        assert res["statuses"][str(card_a)] == {"job_id": job_a["id"], "status": "pending"}
        assert res["statuses"][str(card_b)]["status"] == "error"

    def test_companion_error_surfaces_even_though_root_is_done(self, client):
        card_id = _make_card(spec="s")
        root = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()
        client.post(f"/api/bridge/jobs/{root['id']}/complete", json={"result": "done"})
        companion = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/web-repo", "depends_on_job_id": root["id"],
        }).json()
        client.post(f"/api/bridge/jobs/{companion['id']}/error", json={"result": "boom"})

        res = client.get("/api/bridge/jobs/status").json()
        # Reports the root job's id (the card-level "current job") but the more urgent status.
        assert res["statuses"][str(card_id)] == {"job_id": root["id"], "status": "error"}

    def test_root_done_and_companion_done_reports_done(self, client):
        card_id = _make_card(spec="s")
        root = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()
        client.post(f"/api/bridge/jobs/{root['id']}/complete", json={"result": "done"})
        companion = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/web-repo", "depends_on_job_id": root["id"],
        }).json()
        client.post(f"/api/bridge/jobs/{companion['id']}/complete", json={"result": "done"})

        res = client.get("/api/bridge/jobs/status").json()
        assert res["statuses"][str(card_id)]["status"] == "done"

    def test_companion_from_a_superseded_root_does_not_leak_in(self, client):
        """A companion tied to an old root shouldn't affect the status of a newer root that
        superseded it (e.g. the card was re-run from scratch after the first root+companion
        pairing errored out)."""
        card_id = _make_card(spec="s")
        old_root = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()
        old_companion = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/web-repo", "depends_on_job_id": old_root["id"],
        }).json()
        client.post(f"/api/bridge/jobs/{old_companion['id']}/error", json={"result": "boom"})
        new_root = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()

        res = client.get("/api/bridge/jobs/status").json()
        assert res["statuses"][str(card_id)] == {"job_id": new_root["id"], "status": "pending"}


# ── GET /api/bridge/jobs/dashboard ───────────────────────────────────────────

class TestBridgeJobsDashboardEndpoint:

    def _set_updated_at(self, job_id, when):
        with TestSession() as db:
            job = db.query(models.BridgeJob).filter_by(id=job_id).first()
            job.updated_at = when
            db.commit()

    def test_no_jobs_returns_empty(self, client):
        res = client.get("/api/bridge/jobs/dashboard")
        assert res.status_code == 200
        assert res.json() == {"jobs": []}

    def test_includes_pending_job_with_card_title(self, client):
        card_id = _make_card(title="Fix login bug", spec="s")
        job = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()

        res = client.get("/api/bridge/jobs/dashboard").json()
        assert len(res["jobs"]) == 1
        entry = res["jobs"][0]
        assert entry["id"] == job["id"]
        assert entry["card_id"] == card_id
        assert entry["card_title"] == "Fix login bug"
        assert entry["status"] == "pending"

    def test_excludes_large_text_fields(self, client):
        card_id = _make_card(spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        entry = client.get("/api/bridge/jobs/dashboard").json()["jobs"][0]
        assert "spec_snapshot" not in entry
        assert "prompt_snapshot" not in entry
        assert "output" not in entry

    def test_active_job_included_regardless_of_age(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        self._set_updated_at(job_id, datetime(2020, 1, 1))

        res = client.get("/api/bridge/jobs/dashboard").json()
        assert len(res["jobs"]) == 1
        assert res["jobs"][0]["status"] == "pending"

    def test_old_finished_job_is_excluded(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "done"})
        self._set_updated_at(job_id, datetime(2020, 1, 1))

        res = client.get("/api/bridge/jobs/dashboard").json()
        assert res["jobs"] == []

    def test_recently_finished_job_is_included(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/error", json={"result": "boom"})

        res = client.get("/api/bridge/jobs/dashboard").json()
        assert len(res["jobs"]) == 1
        assert res["jobs"][0]["status"] == "error"

    def test_active_jobs_sort_above_recent_finished_ones_even_if_older(self, client):
        card_a = _make_card(title="Errored recently", spec="s")
        card_b = _make_card(title="Still running", spec="s")
        error_job_id = client.post("/api/bridge/jobs", json={"card_id": card_a}).json()["id"]
        client.post(f"/api/bridge/jobs/{error_job_id}/error", json={"result": "boom"})
        running_job_id = client.post("/api/bridge/jobs", json={"card_id": card_b}).json()["id"]
        client.get("/api/bridge/jobs/next/pending")  # claims it, sets running
        # The running job's last heartbeat is older than the errored job's, but still
        # within the recent-activity window (an active job's own age never matters, but
        # this keeps the fixture realistic rather than relying on that fact).
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self._set_updated_at(running_job_id, now - timedelta(hours=20))
        self._set_updated_at(error_job_id, now - timedelta(minutes=5))

        res = client.get("/api/bridge/jobs/dashboard").json()
        statuses = [j["status"] for j in res["jobs"]]
        assert statuses == ["running", "error"]

    def test_deleted_card_shows_a_placeholder_title(self, client):
        # A job whose card no longer exists (FK enforcement is off for this app's SQLite
        # connections, so this can genuinely happen -- see database.py) shouldn't 500 the
        # whole dashboard just because one row's title lookup comes up empty.
        with TestSession() as db:
            db.add(models.BridgeJob(card_id=9999, status="pending"))
            db.commit()

        res = client.get("/api/bridge/jobs/dashboard").json()
        assert res["jobs"][0]["card_title"] == "(deleted card)"

    def test_multiple_cards_each_get_their_own_entry(self, client):
        card_a = _make_card(title="Card A", spec="s")
        card_b = _make_card(title="Card B", spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_a})
        client.post("/api/bridge/jobs", json={"card_id": card_b})

        res = client.get("/api/bridge/jobs/dashboard").json()
        titles = {j["card_title"] for j in res["jobs"]}
        assert titles == {"Card A", "Card B"}


# ── GET /api/bridge/repos ────────────────────────────────────────────────────

class TestKnownReposEndpoint:

    def test_no_repos_known_returns_empty_list(self, client):
        res = client.get("/api/bridge/repos")
        assert res.status_code == 200
        assert res.json() == {"repos": []}

    def test_includes_repos_from_synced_engineering_items(self, client):
        _make_eng_item("github:owner/api-repo/issues/1", repo="owner/api-repo")
        res = client.get("/api/bridge/repos")
        assert res.json()["repos"] == ["owner/api-repo"]

    def test_includes_repos_from_job_target_repo(self, client):
        card_id = _make_card(spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id, "target_repo": "owner/web-repo"})
        res = client.get("/api/bridge/repos")
        assert res.json()["repos"] == ["owner/web-repo"]

    def test_dedupes_and_sorts_across_both_sources(self, client):
        _make_eng_item("github:owner/b-repo/issues/1", repo="owner/b-repo")
        card_id = _make_card(spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id, "target_repo": "owner/a-repo"})
        client.post("/api/bridge/jobs", json={"card_id": card_id, "target_repo": "owner/b-repo"})

        res = client.get("/api/bridge/repos")
        assert res.json()["repos"] == ["owner/a-repo", "owner/b-repo"]


# ── POST /api/bridge/jobs/queue-by-tag ────────────────────────────────────────

class TestQueueJobsByTag:

    def test_404_when_tag_not_found(self, client):
        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "nope"})
        assert res.status_code == 404

    def test_queues_jobs_for_tagged_cards_with_spec(self, client):
        _make_tag("work")
        _make_card("Fix login", spec="## Fix\nDo it", tags=("work",))
        _make_card("Fix logout", spec="## Fix\nDo it too", tags=("work",))

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert res.status_code == 200
        data = res.json()
        assert len(data["queued"]) == 2
        titles = {j["card_id"] for j in data["queued"]}
        assert len(titles) == 2
        for job in data["queued"]:
            assert job["status"] == "pending"

    def test_ignores_cards_without_the_tag(self, client):
        _make_tag("work")
        _make_tag("personal")
        _make_card("Work task", spec="s", tags=("work",))
        _make_card("Personal task", spec="s", tags=("personal",))

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert len(res.json()["queued"]) == 1

    def test_skips_cards_without_a_spec(self, client):
        _make_tag("work")
        _make_card("No spec yet", spec=None, tags=("work",))

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        data = res.json()
        assert data["queued"] == []
        assert len(data["skipped_no_spec"]) == 1
        assert data["skipped_no_spec"][0]["title"] == "No spec yet"

    def test_skips_cards_with_an_existing_pending_job(self, client):
        _make_tag("work")
        card_id = _make_card("Already queued", spec="s", tags=("work",))
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        data = res.json()
        assert data["queued"] == []
        assert len(data["skipped_already_queued"]) == 1
        assert data["skipped_already_queued"][0]["id"] == card_id

    def test_skips_cards_with_an_existing_running_job(self, client):
        _make_tag("work")
        card_id = _make_card("Already running", spec="s", tags=("work",))
        client.post("/api/bridge/jobs", json={"card_id": card_id})
        client.get("/api/bridge/jobs/next/pending")  # claims → running

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert res.json()["skipped_already_queued"] != []

    def test_requeues_card_whose_last_job_is_done(self, client):
        """A card whose previous job finished (done/error) is eligible again —
        only pending/running jobs block re-queueing."""
        _make_tag("work")
        card_id = _make_card("Redo this", spec="s", tags=("work",))
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.get("/api/bridge/jobs/next/pending")
        client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "done"})

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert len(res.json()["queued"]) == 1

    def test_skips_completed_cards(self, client):
        _make_tag("work")
        _make_card("Done already", spec="s", tags=("work",), completed=True)

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        data = res.json()
        assert data["queued"] == []
        assert data["skipped_no_spec"] == []
        assert data["skipped_already_queued"] == []

    def test_skips_archived_cards(self, client):
        _make_tag("work")
        _make_card("Archived", spec="s", tags=("work",), archived=True)

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert res.json()["queued"] == []

    def test_tag_match_is_case_insensitive(self, client):
        _make_tag("Work")
        _make_card("Fix login", spec="s", tags=("Work",))

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        assert len(res.json()["queued"]) == 1

    def test_queued_job_has_correct_target_repo_and_spec(self, client):
        _make_tag("work")
        _make_card("GH card", spec="## Do the thing", tags=("work",),
                    external_id="github:owner/myapp/issues/9")

        res = client.post("/api/bridge/jobs/queue-by-tag", json={"tag": "work"})
        job = res.json()["queued"][0]
        assert job["target_repo"] == "owner/myapp"
        assert job["spec_snapshot"] == "## Do the thing"


# ── GET /api/bridge/jobs/{id} ─────────────────────────────────────────────────

class TestGetBridgeJob:

    def test_404_for_missing_job(self, client):
        res = client.get("/api/bridge/jobs/9999")
        assert res.status_code == 404

    def test_returns_correct_shape(self, client):
        card_id = _make_card(spec="s")
        res = client.post("/api/bridge/jobs", json={"card_id": card_id})
        job_id = res.json()["id"]

        res2 = client.get(f"/api/bridge/jobs/{job_id}")
        assert res2.status_code == 200
        data = res2.json()
        assert data["id"] == job_id
        assert data["card_id"] == card_id
        assert data["status"] == "pending"
        assert "created_at" in data


# ── GET /api/bridge/jobs/next/pending ─────────────────────────────────────────

class TestGetNextPending:

    def test_returns_null_when_no_pending_jobs(self, client):
        res = client.get("/api/bridge/jobs/next/pending")
        assert res.status_code == 200
        assert res.json()["job"] is None

    def test_claims_job_and_sets_running(self, client):
        card_id = _make_card(spec="spec")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending")
        assert res.status_code == 200
        job = res.json()["job"]
        assert job is not None
        assert job["status"] == "running"

    def test_includes_prompt_and_spec_in_response(self, client):
        card_id = _make_card("Feature X", spec="## Spec\nDo the thing")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending")
        job = res.json()["job"]
        assert "prompt" in job
        assert "spec" in job
        assert job["prompt"] is not None
        assert "Feature X" in job["prompt"]

    def test_second_call_returns_null(self, client):
        card_id = _make_card(spec="spec")
        client.post("/api/bridge/jobs", json={"card_id": card_id})
        client.get("/api/bridge/jobs/next/pending")  # claims it

        res = client.get("/api/bridge/jobs/next/pending")
        assert res.json()["job"] is None

    def test_lazy_prompt_build_for_telegram_queued_job(self, client):
        """Jobs queued via Telegram have no prompt_snapshot — it should be built lazily."""
        card_id = _make_card("Lazy feature", spec="## Spec\nBuild it")
        # Insert a job without prompt_snapshot (as Telegram does)
        with TestSession() as db:
            job = models.BridgeJob(
                card_id=card_id,
                status="pending",
                spec_snapshot="## Spec\nBuild it",
                prompt_snapshot=None,
                created_at=datetime.now(timezone.utc),
            )
            db.add(job)
            db.commit()

        res = client.get("/api/bridge/jobs/next/pending")
        job_data = res.json()["job"]
        assert job_data is not None
        assert job_data["prompt"] is not None
        assert "Lazy feature" in job_data["prompt"]
        assert "Build it" in job_data["prompt"]

    def test_fifo_order(self, client):
        """Oldest pending job is returned first."""
        card1 = _make_card("First", spec="s1")
        card2 = _make_card("Second", spec="s2")
        client.post("/api/bridge/jobs", json={"card_id": card1})
        client.post("/api/bridge/jobs", json={"card_id": card2})

        res = client.get("/api/bridge/jobs/next/pending")
        assert res.json()["job"]["card_id"] == card1

    def test_includes_card_title_for_slug_generation(self, client):
        card_id = _make_card("Fix login bug", spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending")
        job = res.json()["job"]
        assert job["card_title"] == "Fix login bug"


# ── POST /api/bridge/jobs/{id}/start ─────────────────────────────────────────

class TestStartJob:

    def test_records_branch_and_agent(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/start",
                          json={"branch": "qtask/7-fix-login", "agent": "work-mac"})
        assert res.status_code == 200
        data = res.json()
        assert data["branch_name"] == "qtask/7-fix-login"
        assert data["agent_name"] == "work-mac"

    def test_branch_and_agent_visible_via_get(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start",
                    json={"branch": "qtask/7-fix-login", "agent": "work-mac"})

        res = client.get(f"/api/bridge/jobs/{job_id}")
        assert res.json()["branch_name"] == "qtask/7-fix-login"
        assert res.json()["agent_name"] == "work-mac"

    def test_records_worktree_path(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/start", json={
            "branch": "qtask/7-fix-login", "agent": "work-mac",
            "worktree_path": "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-fix-login",
        })
        assert res.status_code == 200
        assert res.json()["worktree_path"] == "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-fix-login"

    def test_worktree_path_is_optional(self, client):
        """Older bridge versions won't send it -- must not break."""
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/start",
                          json={"branch": "qtask/7-fix-login", "agent": "work-mac"})
        assert res.status_code == 200
        assert res.json()["worktree_path"] is None

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/start",
                          json={"branch": "qtask/1-foo", "agent": "x"})
        assert res.status_code == 404


# ── POST /api/bridge/jobs/{id}/rename-branch ─────────────────────────────────

class TestRenameBranchJob:

    def _make_started_job(self, client, branch="qtask/7-fix-login"):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start",
                   json={"branch": branch, "agent": "work-mac"})
        return job_id

    def test_updates_branch_name(self, client):
        job_id = self._make_started_job(client)

        res = client.post(f"/api/bridge/jobs/{job_id}/rename-branch",
                          json={"branch_name": "qtask/7-better-name"})
        assert res.status_code == 200
        assert res.json()["branch_name"] == "qtask/7-better-name"

    def test_new_name_visible_via_get(self, client):
        job_id = self._make_started_job(client)
        client.post(f"/api/bridge/jobs/{job_id}/rename-branch",
                   json={"branch_name": "qtask/7-better-name"})

        res = client.get(f"/api/bridge/jobs/{job_id}")
        assert res.json()["branch_name"] == "qtask/7-better-name"

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/rename-branch",
                          json={"branch_name": "qtask/1-foo"})
        assert res.status_code == 404

    def test_400_for_empty_branch_name(self, client):
        job_id = self._make_started_job(client)
        res = client.post(f"/api/bridge/jobs/{job_id}/rename-branch", json={"branch_name": "   "})
        assert res.status_code == 400

    def test_400_for_branch_name_with_whitespace(self, client):
        job_id = self._make_started_job(client)
        res = client.post(f"/api/bridge/jobs/{job_id}/rename-branch", json={"branch_name": "bad name"})
        assert res.status_code == 400

    def test_400_for_branch_name_containing_dotdot(self, client):
        job_id = self._make_started_job(client)
        res = client.post(f"/api/bridge/jobs/{job_id}/rename-branch", json={"branch_name": "../escape"})
        assert res.status_code == 400

    def test_400_for_branch_name_starting_with_a_dash(self, client):
        job_id = self._make_started_job(client)
        res = client.post(f"/api/bridge/jobs/{job_id}/rename-branch", json={"branch_name": "-flag-like"})
        assert res.status_code == 400


# ── POST /api/bridge/jobs/{id}/request-rename ────────────────────────────────

class TestRequestJobRename:
    """Webapp-side half of the mid-session branch rename flow (Code tab).
    The bridge-side half (heartbeat noticing the request and doing the actual
    `git branch -m`) is covered in test_bridge_scripts.py."""

    def test_sets_requested_branch_name_on_a_pending_job(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/request-rename",
                          json={"branch_name": "qtask/7-better-name"})
        assert res.status_code == 200
        data = res.json()
        assert data["requested_branch_name"] == "qtask/7-better-name"
        assert data["branch_name"] is None  # not started yet -- nothing to rename directly

    def test_sets_requested_branch_name_on_a_running_job(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start", json={"branch": "qtask/7-original", "agent": "work-mac"})

        res = client.post(f"/api/bridge/jobs/{job_id}/request-rename",
                          json={"branch_name": "qtask/7-renamed"})
        assert res.status_code == 200
        data = res.json()
        assert data["requested_branch_name"] == "qtask/7-renamed"
        assert data["branch_name"] == "qtask/7-original"  # unchanged until the bridge confirms

    def test_visible_in_the_next_heartbeat_response(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start", json={"branch": "qtask/7-original", "agent": "work-mac"})
        client.post(f"/api/bridge/jobs/{job_id}/request-rename", json={"branch_name": "qtask/7-renamed"})

        res = client.post(f"/api/bridge/jobs/{job_id}/heartbeat")
        assert res.status_code == 200
        assert res.json() == {"ok": True, "requested_branch_name": "qtask/7-renamed"}

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/request-rename", json={"branch_name": "qtask/1-foo"})
        assert res.status_code == 404

    def test_400_for_done_job(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start", json={"branch": "qtask/7-original", "agent": "work-mac"})
        client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "done"})

        res = client.post(f"/api/bridge/jobs/{job_id}/request-rename", json={"branch_name": "qtask/7-renamed"})
        assert res.status_code == 400

    def test_400_for_error_job(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/error", json={"result": "boom"})

        res = client.post(f"/api/bridge/jobs/{job_id}/request-rename", json={"branch_name": "qtask/7-renamed"})
        assert res.status_code == 400

    def test_400_for_blocked_job(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/error", json={"result": "boom"})
        companion = client.post("/api/bridge/jobs", json={
            "card_id": card_id, "target_repo": "owner/other", "depends_on_job_id": job_id,
        }).json()
        assert companion["status"] == "blocked"

        res = client.post(f"/api/bridge/jobs/{companion['id']}/request-rename",
                          json={"branch_name": "qtask/7-renamed"})
        assert res.status_code == 400

    def test_400_for_empty_branch_name(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        res = client.post(f"/api/bridge/jobs/{job_id}/request-rename", json={"branch_name": "   "})
        assert res.status_code == 400

    def test_400_for_branch_name_with_whitespace(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        res = client.post(f"/api/bridge/jobs/{job_id}/request-rename", json={"branch_name": "bad name"})
        assert res.status_code == 400


# ── POST /api/bridge/jobs/{id}/fix ───────────────────────────────────────────

class TestQueueFixJob:

    def _make_started_job(self, client, card_id=None, external_id=None):
        """A job that's already run to completion and recorded a resumable worktree -- status
        "done", not "pending", so it doesn't shadow a later fix job in next/pending's query
        (oldest pending job wins there, and this one was created first)."""
        card_id = card_id or _make_card(spec="s", external_id=external_id)
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start", json={
            "branch": "qtask/7-fix-login", "agent": "work-mac",
            "worktree_path": "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-fix-login",
        })
        client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "done"})
        return job_id

    def test_queues_a_fix_job_resuming_the_original_worktree(self, client):
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(item_id, github_id=501)
        job_id = self._make_started_job(client, external_id="github:owner/repo/pull/7")

        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [comment_id]})
        assert res.status_code == 200
        data = res.json()
        assert data["resumes_job_id"] == job_id
        assert data["fix_comment_ids"] == [comment_id]
        assert data["status"] == "pending"
        # Copied from the original job at creation time, not left null for /start to fill in.
        assert data["branch_name"] == "qtask/7-fix-login"
        assert data["worktree_path"] == "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-fix-login"

    def test_fix_prompt_includes_comment_body_and_diff_position(self, client):
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(
            item_id, github_id=502, author="coderabbitai[bot]",
            body="Use a set for O(1) lookups.", diff_path="src/auth.js", diff_line=42,
        )
        job_id = self._make_started_job(client, external_id="github:owner/repo/pull/7")

        fix_job_id = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [comment_id]}).json()["id"]
        # prompt_snapshot isn't exposed on the plain job response -- fetch the next pending
        # job instead, matching how the bridge itself receives the actual prompt content.
        pending = client.get("/api/bridge/jobs/next/pending").json()["job"]
        assert pending["id"] == fix_job_id
        assert "Use a set for O(1) lookups." in pending["prompt"]
        assert "src/auth.js:42" in pending["prompt"]
        assert "coderabbitai[bot]" in pending["prompt"]
        assert "not a general invitation to refactor" in pending["prompt"]

    def test_404_for_missing_original_job(self, client):
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(item_id, github_id=503)
        res = client.post("/api/bridge/jobs/99999/fix", json={"comment_ids": [comment_id]})
        assert res.status_code == 404

    def test_400_when_original_job_has_no_worktree(self, client):
        """A job that never ran (still pending, no /start call) has nothing to resume."""
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(item_id, github_id=504)

        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [comment_id]})
        assert res.status_code == 400

    def test_400_for_empty_comment_ids(self, client):
        job_id = self._make_started_job(client)
        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": []})
        assert res.status_code == 400

    def test_409_when_original_job_is_still_running(self, client):
        """Fixing a job that's actively being worked would point a second live agent
        session at the exact same worktree -- must be refused, not just discouraged."""
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.get("/api/bridge/jobs/next/pending")  # flips it to "running"
        client.post(f"/api/bridge/jobs/{job_id}/start", json={
            "branch": "qtask/7-fix-login", "agent": "work-mac",
            "worktree_path": "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-7-fix-login",
        })
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(item_id, github_id=505)

        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [comment_id]})
        assert res.status_code == 409

    def test_409_when_original_job_is_still_pending(self, client):
        """A fix/resume job's worktree_path is copied from its parent at creation time, before
        it's ever run -- so it's still "pending" but already has enough to (incorrectly) pass
        the worktree_path check alone. Covers that case specifically."""
        item_id = _make_eng_item("github:owner/repo/pull/7")
        comment_id = _make_comment(item_id, github_id=506)
        job_id = self._make_started_job(client, external_id="github:owner/repo/pull/7")
        fix_job_id = client.post(f"/api/bridge/jobs/{job_id}/fix",
                                 json={"comment_ids": [comment_id]}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{fix_job_id}/fix", json={"comment_ids": [comment_id]})
        assert res.status_code == 409

    def test_404_for_unknown_comment_id(self, client):
        job_id = self._make_started_job(client)
        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [999999]})
        assert res.status_code == 404

    def test_multiple_comments_all_included_in_prompt(self, client):
        item_id = _make_eng_item("github:owner/repo/pull/7")
        c1 = _make_comment(item_id, github_id=505, body="First fix needed.", diff_path="a.py", diff_line=1)
        c2 = _make_comment(item_id, github_id=506, author="a-human-reviewer",
                            body="Second fix needed.", diff_path="b.py", diff_line=2)
        job_id = self._make_started_job(client, external_id="github:owner/repo/pull/7")

        res = client.post(f"/api/bridge/jobs/{job_id}/fix", json={"comment_ids": [c1, c2]})
        assert res.json()["fix_comment_ids"] == [c1, c2]

        pending = client.get("/api/bridge/jobs/next/pending").json()["job"]
        assert "First fix needed." in pending["prompt"]
        assert "Second fix needed." in pending["prompt"]
        assert "a-human-reviewer" in pending["prompt"]


# ── POST /api/bridge/jobs/{id}/resume ────────────────────────────────────────

class TestQueueResumeJob:

    def _make_started_job(self, client, card_id=None, external_id=None, status="error"):
        """A job that ran and recorded a resumable worktree, then ended abnormally --
        status "error"/"stalled", not "pending", so it doesn't shadow a later resume job in
        next/pending's query."""
        card_id = card_id or _make_card(spec="s", external_id=external_id)
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start", json={
            "branch": "qtask/9-oauth-login", "agent": "work-mac",
            "worktree_path": "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-9-oauth-login",
        })
        if status == "error":
            client.post(f"/api/bridge/jobs/{job_id}/error", json={"result": "claude exited with code 1"})
        elif status == "stalled":
            # No API path sets this directly -- it's a server-side heartbeat-timeout sweep
            # (bridge/stale.py) in real usage. Write it directly for the test.
            with TestSession() as db:
                job = db.query(models.BridgeJob).filter_by(id=job_id).first()
                job.status = "stalled"
                db.commit()
        else:
            client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "done"})
        return job_id

    def test_queues_a_resume_job_resuming_the_original_worktree(self, client):
        job_id = self._make_started_job(client)

        res = client.post(f"/api/bridge/jobs/{job_id}/resume")
        assert res.status_code == 200
        data = res.json()
        assert data["resumes_job_id"] == job_id
        assert data["fix_comment_ids"] is None
        assert data["status"] == "pending"
        assert data["branch_name"] == "qtask/9-oauth-login"
        assert data["worktree_path"] == \
            "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-9-oauth-login"

    def test_resume_prompt_includes_original_spec_and_continuation_framing(self, client):
        card_id = _make_card(spec="## Problem Statement\nImplement OAuth login.")
        job_id = self._make_started_job(client, card_id=card_id)

        resume_job_id = client.post(f"/api/bridge/jobs/{job_id}/resume").json()["id"]
        pending = client.get("/api/bridge/jobs/next/pending").json()["job"]
        assert pending["id"] == resume_job_id
        assert "Resuming an interrupted session" in pending["prompt"]
        assert "git log" in pending["prompt"]
        assert "Implement OAuth login." in pending["prompt"]

    def test_resume_job_has_no_fix_comment_ids(self, client):
        job_id = self._make_started_job(client)
        res = client.post(f"/api/bridge/jobs/{job_id}/resume")
        assert res.json()["fix_comment_ids"] is None

    def test_404_for_missing_original_job(self, client):
        res = client.post("/api/bridge/jobs/99999/resume")
        assert res.status_code == 404

    def test_400_when_original_job_has_no_worktree(self, client):
        """A job that never ran (still pending, no /start call) has nothing to resume."""
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/resume")
        assert res.status_code == 400

    def test_resumable_after_stalled_status_too(self, client):
        job_id = self._make_started_job(client, status="stalled")
        res = client.post(f"/api/bridge/jobs/{job_id}/resume")
        assert res.status_code == 200

    def test_409_when_original_job_is_still_running(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.get("/api/bridge/jobs/next/pending")  # flips it to "running"
        client.post(f"/api/bridge/jobs/{job_id}/start", json={
            "branch": "qtask/9-oauth-login", "agent": "work-mac",
            "worktree_path": "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/qtask-9-oauth-login",
        })

        res = client.post(f"/api/bridge/jobs/{job_id}/resume")
        assert res.status_code == 409

    def test_409_when_original_job_is_still_pending(self, client):
        job_id = self._make_started_job(client)
        resume_job_id = client.post(f"/api/bridge/jobs/{job_id}/resume").json()["id"]

        res = client.post(f"/api/bridge/jobs/{resume_job_id}/resume")
        assert res.status_code == 409


# ── POST /api/bridge/jobs/{id}/output ────────────────────────────────────────

class TestJobOutput:

    def _make_running_job(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.get("/api/bridge/jobs/next/pending")  # sets to running
        return job_id

    def test_appends_output_chunks(self, client):
        job_id = self._make_running_job(client)
        client.post(f"/api/bridge/jobs/{job_id}/output", json={"output": "line one\n"})
        client.post(f"/api/bridge/jobs/{job_id}/output", json={"output": "line two\n"})

        res = client.get(f"/api/bridge/jobs/{job_id}")
        assert "line one" in res.json()["output"]
        assert "line two" in res.json()["output"]

    def test_truncates_to_200_lines(self, client):
        job_id = self._make_running_job(client)
        # Post 250 lines in one chunk
        big_output = "\n".join(f"line {i}" for i in range(250))
        client.post(f"/api/bridge/jobs/{job_id}/output", json={"output": big_output})

        res = client.get(f"/api/bridge/jobs/{job_id}")
        stored = res.json()["output"]
        assert stored.count("\n") <= 200
        # Should keep the tail (most recent lines)
        assert "line 249" in stored
        assert "line 0" not in stored

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/output", json={"output": "x"})
        assert res.status_code == 404


# ── POST /api/bridge/jobs/{id}/complete ───────────────────────────────────────

class TestCompleteJob:

    def test_sets_status_done_and_result(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.get("/api/bridge/jobs/next/pending")  # set to running

        res = client.post(f"/api/bridge/jobs/{job_id}/complete",
                          json={"result": "https://github.com/owner/repo/pull/42"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "done"
        assert data["result"] == "https://github.com/owner/repo/pull/42"

    def test_empty_result_allowed(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/complete", json={})
        assert res.status_code == 200
        assert res.json()["result"] == ""

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/complete", json={})
        assert res.status_code == 404

    def test_stores_diff_summary(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/complete", json={
            "result": "done", "diff_summary": "api/routes.py | 42 ++++++++\n1 file changed",
        })
        assert res.status_code == 200
        assert res.json()["diff_summary"] == "api/routes.py | 42 ++++++++\n1 file changed"

    def test_diff_summary_defaults_to_empty_string(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "done"})
        assert res.json()["diff_summary"] == ""


# ── POST /api/bridge/jobs/{id}/error ─────────────────────────────────────────

class TestErrorJob:

    def test_sets_status_error_with_message(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/error",
                          json={"result": "claude not found on PATH"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "error"
        assert "claude" in data["result"]

    def test_default_error_message_when_empty(self, client):
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]

        res = client.post(f"/api/bridge/jobs/{job_id}/error", json={})
        assert res.status_code == 200
        assert res.json()["status"] == "error"

    def test_404_for_missing_job(self, client):
        res = client.post("/api/bridge/jobs/9999/error", json={})
        assert res.status_code == 404


# ── GET /api/bridge/jobs/card/{id}/latest ────────────────────────────────────

class TestLatestCardJob:

    def test_returns_null_when_no_jobs(self, client):
        res = client.get("/api/bridge/jobs/card/9999/latest")
        assert res.status_code == 200
        assert res.json()["job"] is None

    def test_returns_most_recent_job(self, client):
        card_id = _make_card(spec="s")
        client.post("/api/bridge/jobs", json={"card_id": card_id})
        client.get("/api/bridge/jobs/next/pending")
        client.post(f"/api/bridge/jobs/1/complete", json={"result": "done1"})
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get(f"/api/bridge/jobs/card/{card_id}/latest")
        assert res.status_code == 200
        job = res.json()["job"]
        assert job is not None
        # Most recent job is the second one (pending), not the first (done)
        assert job["status"] == "pending"


# ── GET /api/bridge/jobs/by-worktree ─────────────────────────────────────────

class TestLatestWorktreeJob:

    def test_returns_null_when_no_matching_worktree(self, client):
        res = client.get("/api/bridge/jobs/by-worktree", params={"path": "/nowhere"})
        assert res.status_code == 200
        assert res.json()["job"] is None

    def test_finds_the_job_by_worktree_path_even_with_a_custom_branch_name(self, client):
        """The whole reason this is keyed by worktree_path rather than parsing the branch
        name for a card id -- a Phase 1 custom branch name isn't reliably parseable."""
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start", json={
            "branch": "totally-custom-name", "agent": "work-mac",
            "worktree_path": "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/totally-custom-name",
        })

        res = client.get("/api/bridge/jobs/by-worktree", params={
            "path": "/Users/dev/.local/share/qtask-bridge/worktrees/myapp/totally-custom-name",
        })
        assert res.status_code == 200
        assert res.json()["job"]["id"] == job_id

    def test_returns_the_most_recent_job_for_that_worktree(self, client):
        """A worktree gets reused across fix/resume jobs -- the latest one wins."""
        card_id = _make_card(spec="s")
        job_id = client.post("/api/bridge/jobs", json={"card_id": card_id}).json()["id"]
        client.post(f"/api/bridge/jobs/{job_id}/start", json={
            "branch": "qtask/1-foo", "agent": "work-mac", "worktree_path": "/wt/1-foo",
        })
        client.post(f"/api/bridge/jobs/{job_id}/complete", json={"result": "done"})
        resume_job_id = client.post(f"/api/bridge/jobs/{job_id}/resume").json()["id"]

        res = client.get("/api/bridge/jobs/by-worktree", params={"path": "/wt/1-foo"})
        assert res.json()["job"]["id"] == resume_job_id


# ── GET /api/bridge/jobs/next/pending?repos= ─────────────────────────────────

class TestReposFilter:

    def test_returns_matching_repo_job(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/myapp/issues/1")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/myapp")
        assert res.json()["job"] is not None

    def test_excludes_other_repo_job(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/other/issues/1")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/myapp")
        assert res.json()["job"] is None

    def test_null_target_repo_returned_when_filter_set(self, client):
        """Jobs with no target_repo (no GitHub link) should be returned to any bridge."""
        card_id = _make_card(spec="s")  # no external_id → target_repo=None
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/myapp")
        assert res.json()["job"] is not None

    def test_no_filter_returns_any_repo_job(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/other/issues/1")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending")
        assert res.json()["job"] is not None

    def test_multiple_repos_in_filter(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/second/issues/5")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/first,owner/second")
        assert res.json()["job"] is not None

    def test_multiple_repos_excludes_non_matching(self, client):
        card_id = _make_card(spec="s", external_id="github:owner/third/issues/5")
        client.post("/api/bridge/jobs", json={"card_id": card_id})

        res = client.get("/api/bridge/jobs/next/pending?repos=owner/first,owner/second")
        assert res.json()["job"] is None
