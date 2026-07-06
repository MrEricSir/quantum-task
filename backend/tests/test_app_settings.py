"""
Unit tests for AppSetting key constants and the WithingsCredentials model.

No network calls, no Withings API required.
"""

import sys
import os
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Base
import models
import app_setting_keys as keys


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ── AppSetting key constants ──────────────────────────────────────────────────

class TestAppSettingKeys:
    def test_all_constants_are_non_empty_strings(self):
        for attr in vars(keys):
            if attr.startswith("_"):
                continue
            val = getattr(keys, attr)
            if callable(val):
                continue
            assert isinstance(val, str), f"{attr} should be a str, got {type(val)}"
            assert val, f"{attr} should be non-empty"

    def test_no_duplicate_values(self):
        values = [
            v for k, v in vars(keys).items()
            if not k.startswith("_") and isinstance(v, str) and not callable(v)
        ]
        assert len(values) == len(set(values)), "duplicate AppSetting key values detected"

    def test_known_keys_present(self):
        assert keys.DISCOVERY_INTERESTS == "event_discovery_interests"
        assert keys.EXPORT_TOKEN == "export_token"
        assert keys.VAPID_PRIVATE_KEY == "vapid_private_key"
        assert keys.VAPID_PUBLIC_KEY == "vapid_public_key"
        assert keys.GITHUB_TOKEN == "github_token"
        assert keys.GITHUB_REPOS == "github_repos"
        assert keys.WITHINGS_HEALTH_GOALS == "withings_health_goals"
        assert keys.STREAK_DAYS_V1 == "streak_days_v1"


# ── WithingsCredentials model ─────────────────────────────────────────────────

def _creds(**overrides) -> dict:
    defaults = {
        "access_token": "tok_abc",
        "token_type": "Bearer",
        "refresh_token": "ref_xyz",
        "userid": 12345,
        "client_id": "client_id_123",
        "consumer_secret": "secret_456",
        "expires_in": 10800,
    }
    defaults.update(overrides)
    return defaults


class TestWithingsCredentialsModel:

    def test_save_new_credentials(self, db):
        db.add(models.WithingsCredentials(**_creds()))
        db.commit()
        row = db.query(models.WithingsCredentials).first()
        assert row is not None
        assert row.access_token == "tok_abc"
        assert row.refresh_token == "ref_xyz"
        assert row.userid == 12345
        assert row.token_type == "Bearer"
        assert row.expires_in == 10800

    def test_no_credentials_returns_none(self, db):
        assert db.query(models.WithingsCredentials).first() is None

    def test_last_synced_defaults_null(self, db):
        db.add(models.WithingsCredentials(**_creds()))
        db.commit()
        row = db.query(models.WithingsCredentials).first()
        assert row.last_synced is None

    def test_last_synced_stored(self, db):
        ts = datetime(2026, 6, 20, 12, 0, 0)
        db.add(models.WithingsCredentials(**_creds(), last_synced=ts))
        db.commit()
        row = db.query(models.WithingsCredentials).first()
        assert row.last_synced == ts

    def test_update_tokens_in_place(self, db):
        db.add(models.WithingsCredentials(**_creds()))
        db.commit()
        row = db.query(models.WithingsCredentials).first()
        row.access_token = "new_tok"
        row.refresh_token = "new_ref"
        db.commit()
        updated = db.query(models.WithingsCredentials).first()
        assert updated.access_token == "new_tok"
        assert updated.refresh_token == "new_ref"
        assert db.query(models.WithingsCredentials).count() == 1

    def test_delete_credentials(self, db):
        db.add(models.WithingsCredentials(**_creds()))
        db.commit()
        db.query(models.WithingsCredentials).delete()
        db.commit()
        assert db.query(models.WithingsCredentials).first() is None


# ── _load_credentials_dict helper ────────────────────────────────────────────

class TestLoadCredentialsDict:
    def test_missing_returns_none(self, db):
        from routers.withings import _load_credentials_dict
        assert _load_credentials_dict(db) is None

    def test_roundtrip(self, db):
        from routers.withings import _load_credentials_dict
        db.add(models.WithingsCredentials(**_creds()))
        db.commit()
        result = _load_credentials_dict(db)
        assert result is not None
        assert result["access_token"] == "tok_abc"
        assert result["refresh_token"] == "ref_xyz"
        assert result["userid"] == 12345
        assert result["client_id"] == "client_id_123"
        assert result["expires_in"] == 10800

    def test_returns_dict_not_model(self, db):
        from routers.withings import _load_credentials_dict
        db.add(models.WithingsCredentials(**_creds()))
        db.commit()
        result = _load_credentials_dict(db)
        assert isinstance(result, dict)


# ── _save_credentials_from_dict helper ───────────────────────────────────────

class TestSaveCredentialsFromDict:
    def test_saves_new_row(self, db):
        from routers.withings import _save_credentials_from_dict
        _save_credentials_from_dict(db, _creds())
        assert db.query(models.WithingsCredentials).count() == 1
        row = db.query(models.WithingsCredentials).first()
        assert row.access_token == "tok_abc"

    def test_updates_existing_row(self, db):
        from routers.withings import _save_credentials_from_dict
        _save_credentials_from_dict(db, _creds())
        _save_credentials_from_dict(db, _creds(access_token="tok_2", refresh_token="ref_2"))
        assert db.query(models.WithingsCredentials).count() == 1
        row = db.query(models.WithingsCredentials).first()
        assert row.access_token == "tok_2"
        assert row.refresh_token == "ref_2"
