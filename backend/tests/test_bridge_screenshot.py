"""
Tests for the visual-verification screenshot endpoint:
  POST /api/bridge/jobs/{id}/screenshot  -- stores the base64 PNG, best-effort forwards it
                                            to Telegram

The CLI-side capture logic (shelling out to `npx playwright screenshot`) is covered in
test_bridge_scripts.py's TestCapturePreviewScreenshot -- this file owns only the endpoint,
its effect on the job row, and the Telegram-forwarding side effect. See PRODUCT_NOTES.md's
"Visual verification" entry.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import base64
import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app_setting_keys as keys
import models
from main import app
from deps import get_db

# `bridge/__init__.py` does `from bridge.router import router`, which reassigns the
# `bridge.router` package attribute to the APIRouter instance itself -- `import bridge.router`
# would then hand back that instance, not the module, once `bridge` has been imported.
# importlib.import_module() goes through sys.modules instead, so it reliably returns the real
# module regardless of that shadowing.
bridge_router = importlib.import_module("bridge.router")

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


def _make_card(title="Feature"):
    with TestSession() as db:
        card = models.Card(title=title, section="today", position=0, spec="## Spec\ndo it")
        db.add(card)
        db.commit()
        db.refresh(card)
        return card.id


def _make_job(card_id, status="done"):
    with TestSession() as db:
        job = models.BridgeJob(
            card_id=card_id, status=status, created_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id


def _configure_telegram():
    with TestSession() as db:
        db.add(models.AppSetting(key=keys.TELEGRAM_BOT_TOKEN, value="tok"))
        db.add(models.AppSetting(key=keys.TELEGRAM_CHAT_ID, value="123"))
        db.commit()


_FAKE_PNG_BYTES = b"\x89PNG\r\n fake png bytes"
_FAKE_PNG_B64 = base64.b64encode(_FAKE_PNG_BYTES).decode("ascii")


class TestPostJobScreenshotEndpoint:

    def test_404_for_unknown_job(self, client):
        res = client.post("/api/bridge/jobs/999999/screenshot", json={"image_base64": _FAKE_PNG_B64})
        assert res.status_code == 404

    def test_sets_screenshot_data(self, client):
        card_id = _make_card()
        job_id = _make_job(card_id)

        res = client.post(f"/api/bridge/jobs/{job_id}/screenshot", json={"image_base64": _FAKE_PNG_B64})
        assert res.status_code == 200
        assert res.json()["screenshot_data"] == _FAKE_PNG_B64

        with TestSession() as db:
            assert db.query(models.BridgeJob).filter_by(id=job_id).first().screenshot_data == _FAKE_PNG_B64

    def test_never_touches_the_jobs_own_status(self, client):
        card_id = _make_card()
        job_id = _make_job(card_id, status="done")

        res = client.post(f"/api/bridge/jobs/{job_id}/screenshot", json={"image_base64": _FAKE_PNG_B64})

        assert res.json()["status"] == "done"

    def test_sends_telegram_photo_when_configured(self, client, monkeypatch):
        _configure_telegram()
        card_id = _make_card(title="Fix ranking bug")
        job_id = _make_job(card_id)
        calls = []
        monkeypatch.setattr(bridge_router, "send_photo",
                            lambda token, chat_id, image_bytes, caption=None: calls.append(
                                (token, chat_id, image_bytes, caption)) or True)

        client.post(f"/api/bridge/jobs/{job_id}/screenshot", json={"image_base64": _FAKE_PNG_B64})

        assert len(calls) == 1
        token, chat_id, image_bytes, caption = calls[0]
        assert token == "tok"
        assert chat_id == "123"
        assert image_bytes == _FAKE_PNG_BYTES
        assert "Fix ranking bug" in caption

    def test_does_not_send_telegram_photo_when_unconfigured(self, client, monkeypatch):
        card_id = _make_card()
        job_id = _make_job(card_id)
        calls = []
        monkeypatch.setattr(bridge_router, "send_photo",
                            lambda *a, **k: calls.append(1) or True)

        res = client.post(f"/api/bridge/jobs/{job_id}/screenshot", json={"image_base64": _FAKE_PNG_B64})

        assert res.status_code == 200
        assert calls == []

    def test_endpoint_still_succeeds_if_telegram_send_fails(self, client, monkeypatch):
        _configure_telegram()
        card_id = _make_card()
        job_id = _make_job(card_id)
        monkeypatch.setattr(bridge_router, "send_photo", lambda *a, **k: False)

        res = client.post(f"/api/bridge/jobs/{job_id}/screenshot", json={"image_base64": _FAKE_PNG_B64})

        assert res.status_code == 200
        with TestSession() as db:
            assert db.query(models.BridgeJob).filter_by(id=job_id).first().screenshot_data == _FAKE_PNG_B64
