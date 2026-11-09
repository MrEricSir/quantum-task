"""
Tests for push.py's VAPID key management (ensure_vapid_keys/get_vapid_public_key),
covering the switch from raw AppSetting queries to Settings(db).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from push import ensure_vapid_keys, get_vapid_public_key
from settings import Settings


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_get_vapid_public_key_is_none_before_generation(db):
    assert get_vapid_public_key(db) is None


def test_ensure_vapid_keys_generates_and_persists_a_key_pair(db):
    ensure_vapid_keys(db)

    settings = Settings(db)
    assert settings.vapid_private_key
    assert settings.vapid_public_key
    assert get_vapid_public_key(db) == settings.vapid_public_key


def test_ensure_vapid_keys_does_not_regenerate_once_already_present(db):
    ensure_vapid_keys(db)
    first_public_key = get_vapid_public_key(db)

    ensure_vapid_keys(db)

    assert get_vapid_public_key(db) == first_public_key
