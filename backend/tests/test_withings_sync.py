"""
Tests for routers.withings.do_sync's concurrency-safety and the proactive
reauth notification -- both added after a real production incident: two
independent, unsynchronized triggers (the in-process 2-hour scheduler loop
and the hourly Cloud Scheduler job) both calling do_sync() raced on
refreshing the same stored OAuth token. Withings rotates the refresh token
on every use, so a concurrent second refresh redeems an already-spent
token -- confirmed in production logs via a "Same arguments in less than
10 seconds" rejection followed by a multi-day stretch of
"invalid_refresh_token" failures recurring every ~2 hours until a manual
reconnect recovered it.

No real Withings API calls -- all credentials are synthetic and
_do_sync_impl / _refresh_token are monkeypatched per test.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import threading
import time
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app_setting_keys as setting_keys
import models
import routers.withings as withings

# ── In-memory DB ──────────────────────────────────────────────────────────────

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=test_engine)
    yield
    models.Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db():
    session = TestingSessionLocal()
    yield session
    session.close()


def _add_credentials(db, **overrides) -> models.WithingsCredentials:
    defaults = dict(
        access_token="tok_abc", token_type="Bearer", refresh_token="ref_xyz",
        userid=12345, client_id="client123", consumer_secret="secret456",
        expires_in=10800,
    )
    defaults.update(overrides)
    row = models.WithingsCredentials(**defaults)
    db.add(row)
    db.commit()
    return row


def _set_telegram_config(db, token="tg-token", chat_id="tg-chat"):
    db.add(models.AppSetting(key=setting_keys.TELEGRAM_BOT_TOKEN, value=token))
    db.add(models.AppSetting(key=setting_keys.TELEGRAM_CHAT_ID, value=chat_id))
    db.commit()


# ── Locking: only one do_sync() executes at a time ─────────────────────────────

class TestSyncLocking:

    def test_concurrent_call_is_skipped_not_raced(self, db):
        """The exact scenario from the production incident: a second
        do_sync() call arriving while one is already in flight must return
        immediately, not proceed to read/refresh the same stored token."""
        release_first = threading.Event()
        entered_first = threading.Event()
        call_count = []

        def fake_impl(db_arg):
            call_count.append(1)
            entered_first.set()
            release_first.wait(timeout=5)
            return {"ok": True, "synced": {}}

        with patch.object(withings, "_do_sync_impl", side_effect=fake_impl):
            first_result = {}

            def run_first():
                first_result["value"] = withings.do_sync(db)

            t = threading.Thread(target=run_first)
            t.start()
            assert entered_first.wait(timeout=5), "first call never entered the impl"

            # Second call while the first is still "in flight" (holding the lock).
            second_result = withings.do_sync(db)

            release_first.set()
            t.join(timeout=5)

        assert second_result == {"ok": False, "error": "sync_in_progress"}
        assert first_result["value"] == {"ok": True, "synced": {}}
        assert call_count == [1], "the impl ran more than once -- the lock did not prevent a race"

    def test_lock_released_after_success_allows_next_call(self, db):
        with patch.object(withings, "_do_sync_impl", return_value={"ok": True, "synced": {}}) as impl:
            withings.do_sync(db)
            withings.do_sync(db)
        assert impl.call_count == 2

    def test_lock_released_even_if_impl_raises(self, db):
        with patch.object(withings, "_do_sync_impl", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                withings.do_sync(db)
        # Lock must still be free afterward -- a crash in the impl must not
        # leave every future sync permanently skipped.
        with patch.object(withings, "_do_sync_impl", return_value={"ok": True, "synced": {}}) as impl:
            result = withings.do_sync(db)
        assert result == {"ok": True, "synced": {}}
        assert impl.call_count == 1


# ── Proactive reauth notification ──────────────────────────────────────────────

class TestReauthNotification:

    def _force_auth_error(self, monkeypatch):
        def raise_auth_error(creds_data, db):
            raise withings._TokenAuthError("Token rejected: HTTP 401")
        monkeypatch.setattr(withings, "_refresh_token", raise_auth_error)
        monkeypatch.setattr(withings, "WITHINGS_CLIENT_ID", "cid")
        monkeypatch.setattr(withings, "WITHINGS_SECRET", "secret")

    def test_notifies_once_on_invalid_token(self, db, monkeypatch):
        _add_credentials(db)
        _set_telegram_config(db)
        self._force_auth_error(monkeypatch)

        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            result = withings.do_sync(db)

        assert result == {"ok": False, "error": "invalid_token"}
        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == "tg-token"
        assert mock_send.call_args[0][1] == "tg-chat"

        s = db.query(models.AppSetting).filter_by(
            key=setting_keys.WITHINGS_AUTH_FAILURE_NOTIFIED
        ).first()
        assert s.value == "1"

    def test_does_not_renotify_on_repeated_failure(self, db, monkeypatch):
        _add_credentials(db)
        _set_telegram_config(db)
        self._force_auth_error(monkeypatch)

        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            withings.do_sync(db)
            withings.do_sync(db)
            withings.do_sync(db)

        mock_send.assert_called_once()

    def test_no_send_message_call_when_telegram_not_configured(self, db, monkeypatch):
        _add_credentials(db)
        # No telegram config saved.
        self._force_auth_error(monkeypatch)

        with patch("telegram.scheduler.send_message") as mock_send:
            result = withings.do_sync(db)

        assert result == {"ok": False, "error": "invalid_token"}
        mock_send.assert_not_called()
        # Dedup flag is still set even without telegram configured -- harmless,
        # and correct if telegram gets configured later before the next failure.
        s = db.query(models.AppSetting).filter_by(
            key=setting_keys.WITHINGS_AUTH_FAILURE_NOTIFIED
        ).first()
        assert s.value == "1"

    def test_flag_clears_and_renotifies_after_recovery(self, db, monkeypatch):
        _add_credentials(db)
        _set_telegram_config(db)
        self._force_auth_error(monkeypatch)

        with patch("telegram.scheduler.send_message", return_value=True) as mock_send:
            withings.do_sync(db)
        assert mock_send.call_count == 1

        # Recover: a successful refresh clears the dedup flag.
        def succeed(creds_data, db_arg):
            new_creds = {**creds_data, "access_token": "new_tok", "refresh_token": "new_ref"}
            return new_creds
        monkeypatch.setattr(withings, "_refresh_token", succeed)
        with patch.object(withings, "_load_credentials_dict", return_value={
            "access_token": "tok_abc", "token_type": "Bearer", "refresh_token": "ref_xyz",
            "userid": 12345, "client_id": "client123", "consumer_secret": "secret456",
            "expires_in": 10800,
        }):
            with patch.object(withings, "_withings_get", return_value={}):
                withings.do_sync(db)

        s = db.query(models.AppSetting).filter_by(
            key=setting_keys.WITHINGS_AUTH_FAILURE_NOTIFIED
        ).first()
        assert s.value == "0"

        # A fresh failure after recovery notifies again, not silently.
        self._force_auth_error(monkeypatch)
        with patch("telegram.scheduler.send_message", return_value=True) as mock_send2:
            withings.do_sync(db)
        mock_send2.assert_called_once()
