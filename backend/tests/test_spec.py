"""
Tests for the spec generation endpoint (POST /api/cards/{id}/spec/generate).

All LLM calls are mocked — no real API calls made.
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

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

FAKE_SPEC = (
    "## Problem Statement\nThe login breaks.\n\n"
    "## Context & Background\nOAuth was removed.\n\n"
    "## Acceptance Criteria\n- [ ] Users can log in\n\n"
    "## Technical Approach\nRe-add OAuth.\n\n"
    "## Files Likely Involved\nrouters/auth.py\n\n"
    "## Open Questions\nNone."
)


def _mock_llm(spec_text=FAKE_SPEC):
    """Return a mock llm_client() that produces spec_text."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = spec_text
    mock_client.chat.completions.create.return_value = mock_resp
    return mock_client


def _make_card(title="Feature", description=None, external_id=None):
    with TestSession() as db:
        card = models.Card(title=title, section="today", position=0,
                           description=description, external_id=external_id)
        db.add(card)
        db.commit()
        db.refresh(card)
        return card.id


def _make_eng_item(external_id, body=None, number=1, repo="owner/repo"):
    with TestSession() as db:
        item = models.EngineeringItem(
            external_id=external_id,
            title="Test issue",
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


def _add_comment(item_id, author="alice", body="LGTM", created_at=None):
    # created_at defaults to "now" but accepts an explicit value -- two comments added
    # back-to-back in the same test can otherwise land on the same wall-clock instant,
    # making their relative order (which the ordering test below depends on) a race
    # rather than a guarantee.
    created_at = created_at or datetime.now(timezone.utc)
    with TestSession() as db:
        c = models.EngineeringItemComment(
            item_id=item_id,
            github_id=hash(body) % 100000,
            author=author,
            body=body,
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(c)
        db.commit()


def _add_thread_messages(card_id, messages):
    with TestSession() as db:
        thread = models.CardThread(
            card_id=card_id,
            messages=json.dumps(messages),
            created_at=datetime.now(timezone.utc),
        )
        db.add(thread)
        db.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestGenerateSpec:

    def test_404_when_card_not_found(self, client):
        with patch("assist.generate.llm_client", return_value=_mock_llm()):
            res = client.post("/api/cards/9999/spec/generate")
        assert res.status_code == 404

    def test_returns_spec_text(self, client):
        card_id = _make_card("Auth feature")
        with patch("assist.generate.llm_client", return_value=_mock_llm()):
            res = client.post(f"/api/cards/{card_id}/spec/generate")
        assert res.status_code == 200
        assert res.json()["spec"] == FAKE_SPEC

    def test_persists_spec_to_card(self, client):
        card_id = _make_card("My feature")
        with patch("assist.generate.llm_client", return_value=_mock_llm()):
            client.post(f"/api/cards/{card_id}/spec/generate")
        with TestSession() as db:
            card = db.query(models.Card).filter_by(id=card_id).first()
            assert card.spec == FAKE_SPEC

    def test_user_message_includes_task_title(self, client):
        card_id = _make_card("Unique Feature Name XYZ")
        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "Unique Feature Name XYZ" in user_content

    def test_system_prompt_uses_spec_system(self, client):
        card_id = _make_card("Feature")
        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        messages = mock_client.chat.completions.create.call_args[1]["messages"]
        system_content = next(m["content"] for m in messages if m["role"] == "system")
        assert "Acceptance Criteria" in system_content
        assert "Problem Statement" in system_content

    def test_user_message_includes_developer_notes(self, client):
        card_id = _make_card("Feature", description="Use JWT, not sessions")
        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        messages = mock_client.chat.completions.create.call_args[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "Use JWT, not sessions" in user_content

    def test_user_message_includes_github_issue_body(self, client):
        ext_id = "github:owner/repo/issues/7"
        card_id = _make_card("GH Feature", external_id=ext_id)
        _make_eng_item(ext_id, body="When the user clicks login, nothing happens.")

        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        messages = mock_client.chat.completions.create.call_args[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "When the user clicks login" in user_content
        assert "owner/repo" in user_content

    def test_user_message_includes_github_comments(self, client):
        ext_id = "github:owner/repo/issues/8"
        card_id = _make_card("GH Feature with comments", external_id=ext_id)
        item_id = _make_eng_item(ext_id, body="The feature description")
        _add_comment(item_id, author="bob", body="We should also handle the edge case with null tokens.")

        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        messages = mock_client.chat.completions.create.call_args[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "null tokens" in user_content
        assert "bob" in user_content

    def test_user_message_includes_prior_thread(self, client):
        card_id = _make_card("Feature with thread")
        _add_thread_messages(card_id, [
            {"role": "user", "content": "What approach should I use?"},
            {"role": "assistant", "content": "Use the repository pattern for data access."},
        ])

        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        messages = mock_client.chat.completions.create.call_args[1]["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "repository pattern" in user_content

    def test_llm_error_returns_502(self, client):
        card_id = _make_card("Feature")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("LLM unavailable")
        with patch("assist.generate.llm_client", return_value=mock_client):
            res = client.post(f"/api/cards/{card_id}/spec/generate")
        assert res.status_code == 502
        assert "LLM" in res.json()["detail"]

    def test_card_without_github_link_works(self, client):
        """Cards with no external_id should still generate a spec from title+notes alone."""
        card_id = _make_card("Standalone feature", description="Build a dark mode toggle")
        with patch("assist.generate.llm_client", return_value=_mock_llm()):
            res = client.post(f"/api/cards/{card_id}/spec/generate")
        assert res.status_code == 200
        assert res.json()["spec"] == FAKE_SPEC

    def test_regenerate_overwrites_existing_spec(self, client):
        card_id = _make_card("Feature")
        with TestSession() as db:
            card = db.query(models.Card).filter_by(id=card_id).first()
            card.spec = "old spec"
            db.commit()

        with patch("assist.generate.llm_client", return_value=_mock_llm("new spec content")):
            client.post(f"/api/cards/{card_id}/spec/generate")

        with TestSession() as db:
            card = db.query(models.Card).filter_by(id=card_id).first()
            assert card.spec == "new spec content"


class TestGenerateSpecPrecedence:
    """Sources are not equally authoritative: the user's own Developer Notes should win
    over a conflicting GitHub issue, and a later comment should win over the original
    issue/PR body on the point it addresses. Previously nothing in the prompt said so --
    everything was flattened into one undifferentiated block (user report)."""

    def test_system_prompt_states_developer_notes_outrank_github(self, client):
        card_id = _make_card("Feature")
        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        messages = mock_client.chat.completions.create.call_args[1]["messages"]
        system_content = next(m["content"] for m in messages if m["role"] == "system")
        assert "most authoritative" in system_content
        assert "Developer Notes" in system_content

    def test_system_prompt_states_later_comments_supersede_the_body(self, client):
        card_id = _make_card("Feature")
        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        messages = mock_client.chat.completions.create.call_args[1]["messages"]
        system_content = next(m["content"] for m in messages if m["role"] == "system")
        assert "supersede" in system_content.lower()

    def test_developer_notes_section_is_labeled_authoritative(self, client):
        card_id = _make_card("Feature", description="Use JWT, not sessions")
        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        user_content = next(
            m["content"] for m in mock_client.chat.completions.create.call_args[1]["messages"]
            if m["role"] == "user"
        )
        assert "Developer Notes (most authoritative" in user_content

    def test_github_description_and_comments_are_labeled_with_precedence_hints(self, client):
        ext_id = "github:owner/repo/issues/9"
        card_id = _make_card("GH Feature", external_id=ext_id)
        item_id = _make_eng_item(ext_id, body="Original description")
        _add_comment(item_id, author="bob", body="Clarification: actually do X instead")

        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        user_content = next(
            m["content"] for m in mock_client.chat.completions.create.call_args[1]["messages"]
            if m["role"] == "user"
        )
        assert "may be superseded by later comments" in user_content
        assert "supersedes the description or an earlier comment" in user_content

    def test_comments_remain_in_chronological_order(self, client):
        """Oldest-first ordering is what makes 'a later comment supersedes an earlier
        one' a coherent instruction -- confirms the DB relationship's own ordering
        (EngineeringItemComment.created_at ascending) still holds, independent of the
        new prompt wording added around it."""
        ext_id = "github:owner/repo/issues/10"
        card_id = _make_card("GH Feature ordering", external_id=ext_id)
        item_id = _make_eng_item(ext_id, body="Original description")
        _add_comment(item_id, author="alice", body="First comment",
                     created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        _add_comment(item_id, author="bob", body="Second comment",
                     created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))

        mock_client = _mock_llm()
        with patch("assist.generate.llm_client", return_value=mock_client):
            client.post(f"/api/cards/{card_id}/spec/generate")

        user_content = next(
            m["content"] for m in mock_client.chat.completions.create.call_args[1]["messages"]
            if m["role"] == "user"
        )
        assert user_content.index("First comment") < user_content.index("Second comment")
