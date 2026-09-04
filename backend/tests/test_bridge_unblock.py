"""
Tests for cross-repo companion job unblocking (bridge/unblock.py) and the surrounding
wiring:
  bridge.unblock.unblock_dependent_jobs
  POST /api/bridge/jobs/{id}/complete    — instant unblock of a waiting companion job
  POST /api/bridge/jobs/check-stale      — safety-net sweep for the rest

A companion job (depends_on_job_id set, targeting a different repo than the card's own
GitHub link) is created "blocked" instead of "pending" -- see BRIDGE_CROSS_REPO_JOBS.md
Phase 1/2. This covers the transition back to "pending" once the upstream job it depends
on reaches "done".
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from bridge.unblock import unblock_dependent_jobs
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

def _make_card():
    with TestSession() as db:
        card = models.Card(title="Feature", section="today", position=0, spec="## Spec\ndo it")
        db.add(card)
        db.commit()
        db.refresh(card)
        return card.id


def _make_job(card_id, status="pending", target_repo=None, depends_on_job_id=None,
              result=None, branch_name=None, prompt_snapshot="base prompt", diff_summary=None):
    with TestSession() as db:
        job = models.BridgeJob(
            card_id=card_id,
            status=status,
            target_repo=target_repo,
            depends_on_job_id=depends_on_job_id,
            result=result,
            branch_name=branch_name,
            prompt_snapshot=prompt_snapshot,
            diff_summary=diff_summary,
            created_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id


def _status(job_id):
    with TestSession() as db:
        return db.query(models.BridgeJob).filter_by(id=job_id).first().status


def _prompt(job_id):
    with TestSession() as db:
        return db.query(models.BridgeJob).filter_by(id=job_id).first().prompt_snapshot


# ── bridge.unblock.unblock_dependent_jobs ───────────────────────────────────────

class TestUnblockDependentJobs:

    def test_no_blocked_jobs_is_a_noop(self):
        with TestSession() as db:
            assert unblock_dependent_jobs(db) == []

    def test_upstream_done_unblocks_downstream_to_pending(self):
        card_id = _make_card()
        upstream = _make_job(card_id, status="done", target_repo="owner/api", result="Added endpoint.")
        downstream = _make_job(card_id, status="blocked", target_repo="owner/web", depends_on_job_id=upstream)

        with TestSession() as db:
            unblocked = unblock_dependent_jobs(db)
            assert [j.id for j in unblocked] == [downstream]
        assert _status(downstream) == "pending"

    def test_upstream_result_is_appended_to_downstream_prompt(self):
        card_id = _make_card()
        upstream = _make_job(card_id, status="done", target_repo="owner/api", result="Added POST /export.")
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream, prompt_snapshot="Build the UI.")

        with TestSession() as db:
            unblock_dependent_jobs(db)

        prompt = _prompt(downstream)
        assert "Build the UI." in prompt
        assert "Added POST /export." in prompt
        assert "owner/api" in prompt

    def test_upstream_diff_summary_is_appended_to_downstream_prompt(self):
        card_id = _make_card()
        upstream = _make_job(
            card_id, status="done", target_repo="owner/api", result="Added the endpoint.",
            diff_summary="api/routes.py | 42 ++++++++\n1 file changed, 42 insertions(+)",
        )
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream)

        with TestSession() as db:
            unblock_dependent_jobs(db)

        prompt = _prompt(downstream)
        assert "api/routes.py" in prompt
        assert "42 insertions" in prompt

    def test_upstream_with_no_diff_summary_omits_the_files_changed_section(self):
        card_id = _make_card()
        upstream = _make_job(card_id, status="done", target_repo="owner/api", result="Added the endpoint.")
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream)

        with TestSession() as db:
            unblock_dependent_jobs(db)

        assert "Files changed" not in _prompt(downstream)

    def test_upstream_with_no_result_gets_a_fallback_note(self):
        card_id = _make_card()
        upstream = _make_job(card_id, status="done", target_repo="owner/api", result=None)
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream)

        with TestSession() as db:
            unblock_dependent_jobs(db)

        assert "branch directly" in _prompt(downstream)

    def test_upstream_still_running_leaves_downstream_blocked(self):
        card_id = _make_card()
        upstream = _make_job(card_id, status="running", target_repo="owner/api")
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream)

        with TestSession() as db:
            unblocked = unblock_dependent_jobs(db)

        assert unblocked == []
        assert _status(downstream) == "blocked"

    def test_upstream_error_leaves_downstream_blocked(self):
        """A downstream job must NOT auto-run against a broken upstream -- needs a human
        decision, not an automatic one. See BRIDGE_CROSS_REPO_JOBS.md's open questions."""
        card_id = _make_card()
        upstream = _make_job(card_id, status="error", target_repo="owner/api")
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream)

        with TestSession() as db:
            unblocked = unblock_dependent_jobs(db)

        assert unblocked == []
        assert _status(downstream) == "blocked"

    def test_upstream_stalled_leaves_downstream_blocked(self):
        card_id = _make_card()
        upstream = _make_job(card_id, status="stalled", target_repo="owner/api")
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream)

        with TestSession() as db:
            unblocked = unblock_dependent_jobs(db)

        assert unblocked == []
        assert _status(downstream) == "blocked"

    def test_calling_repeatedly_never_reprocesses_the_same_job(self):
        """Self-limiting like check_stale_bridge_jobs -- once unblocked, a job no longer
        matches status == "blocked", so a second sweep is a no-op for it."""
        card_id = _make_card()
        upstream = _make_job(card_id, status="done", target_repo="owner/api", result="done.")
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream)

        with TestSession() as db:
            first = unblock_dependent_jobs(db)
            assert [j.id for j in first] == [downstream]
        with TestSession() as db:
            second = unblock_dependent_jobs(db)
        assert second == []

    def test_upstream_needs_confirmation_still_unblocks_downstream(self):
        """needs_confirmation means the upstream's coding work genuinely finished -- it's
        just flagged for review because its diff touched a configured checkpoint pattern
        (app_setting_keys.CHECKPOINT_PATTERNS). An unrelated companion job shouldn't have
        to wait on that review."""
        card_id = _make_card()
        upstream = _make_job(card_id, status="needs_confirmation", target_repo="owner/api", result="Added endpoint.")
        downstream = _make_job(card_id, status="blocked", target_repo="owner/web", depends_on_job_id=upstream)

        with TestSession() as db:
            unblocked = unblock_dependent_jobs(db)
            assert [j.id for j in unblocked] == [downstream]
        assert _status(downstream) == "pending"

    def test_a_blocked_job_with_no_depends_on_job_id_is_skipped_not_crashed(self):
        """Shouldn't be reachable via the normal job-creation path (status only becomes
        "blocked" when depends_on_job_id is set -- see _queue_job_for_card), but this
        guards the query itself rather than assuming that invariant always holds."""
        card_id = _make_card()
        job_id = _make_job(card_id, status="blocked", depends_on_job_id=None)

        with TestSession() as db:
            unblocked = unblock_dependent_jobs(db)

        assert unblocked == []
        assert _status(job_id) == "blocked"


# ── POST /api/bridge/jobs/{id}/complete — instant unblock ───────────────────────

class TestCompleteEndpointUnblocksCompanion:

    def test_completing_upstream_instantly_unblocks_the_companion_job(self, client):
        card_id = _make_card()
        upstream = _make_job(card_id, status="running", target_repo="owner/api")
        downstream = _make_job(card_id, status="blocked", target_repo="owner/web", depends_on_job_id=upstream)

        res = client.post(f"/api/bridge/jobs/{upstream}/complete", json={"result": "Shipped the endpoint."})
        assert res.status_code == 200

        assert _status(downstream) == "pending"
        assert "Shipped the endpoint." in _prompt(downstream)

    def test_erroring_upstream_does_not_unblock_the_companion_job(self, client):
        card_id = _make_card()
        upstream = _make_job(card_id, status="running", target_repo="owner/api")
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream)

        res = client.post(f"/api/bridge/jobs/{upstream}/error", json={"result": "Build failed."})
        assert res.status_code == 200
        assert _status(downstream) == "blocked"

    def test_needs_confirmation_upstream_instantly_unblocks_the_companion_job(self, client):
        card_id = _make_card()
        upstream = _make_job(card_id, status="running", target_repo="owner/api")
        downstream = _make_job(card_id, status="blocked", target_repo="owner/web", depends_on_job_id=upstream)

        res = client.post(f"/api/bridge/jobs/{upstream}/needs-confirmation", json={
            "result": "Shipped the endpoint.", "matched_paths": ["alembic/versions/0001_x.py"],
        })
        assert res.status_code == 200
        assert res.json()["status"] == "needs_confirmation"
        assert res.json()["checkpoint_matched_paths"] == ["alembic/versions/0001_x.py"]

        assert _status(downstream) == "pending"
        assert "Shipped the endpoint." in _prompt(downstream)


# ── POST /api/bridge/jobs/check-stale — safety-net sweep ────────────────────────

class TestCheckStaleEndpointAlsoSweepsUnblock:

    def test_check_stale_unblocks_a_companion_created_after_upstream_already_done(self, client):
        """The edge case the safety-net sweep exists for: a companion job created against
        an upstream that was already "done" at creation time never gets an inline
        /complete-triggered unblock, since /complete already fired before it existed."""
        card_id = _make_card()
        upstream = _make_job(card_id, status="done", target_repo="owner/api", result="Already shipped.")
        downstream = _make_job(card_id, status="blocked", depends_on_job_id=upstream)

        res = client.post("/api/bridge/jobs/check-stale")
        assert res.status_code == 200
        assert _status(downstream) == "pending"

    def test_check_stale_response_shape_is_unchanged_when_nothing_to_unblock(self, client):
        res = client.post("/api/bridge/jobs/check-stale")
        assert res.status_code == 200
        assert res.json() == {"stalled": 0, "notified": 0}
