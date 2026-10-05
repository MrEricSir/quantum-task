"""
Tests for main.py's background schedulers.

Covers:
  - _expire_stale_experiments -- must resolve "today" from the stored tz_offset, not
    the server's own UTC clock. This is a background cron with no request to read a
    client-local date from (see deps.py/CLAUDE.md's Timezone Handling notes on this
    exact bug class).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import app_setting_keys as keys
import main


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


def _set_tz_offset(minutes):
    with TestingSessionLocal() as db:
        db.add(models.AppSetting(key=keys.BRIEFING_TZ_OFFSET, value=str(minutes)))
        db.commit()


class TestExpireStaleExperiments:
    """_expire_stale_experiments is the daily background backstop for week-rollover
    cleanup. It used to pass server-UTC date.today() straight to
    auto_expire_stale_experiments/check_food_avoidance_habits, which disagreed with the
    real current week right at the Sunday/Monday boundary for anyone west of UTC --
    this cron would see "Monday" in UTC while the user's local day was still Sunday,
    and auto-dismiss (recording an outcome for) the still-active Sunday-week
    experiment. Fixed to resolve "today" from the stored tz_offset instead, same
    pattern as telegram/scheduler.py's check_all()."""

    def test_uses_stored_tz_offset_not_server_utc_clock(self, monkeypatch):
        # A large offset guarantees local "today" differs from server-UTC "today" on
        # most real test runs, not just in a narrow window right at UTC midnight --
        # avoids a flaky, time-of-day-dependent assertion.
        tz_offset = -720  # UTC-12
        _set_tz_offset(tz_offset)
        expected_today = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=tz_offset)
        ).date()
        expected_week = "{}-W{:02d}".format(*expected_today.isocalendar()[:2])

        monkeypatch.setattr(main, "SessionLocal", TestingSessionLocal)
        with patch("routers.correlations.check_food_avoidance_habits") as mock_food, \
             patch("routers.correlations.auto_expire_stale_experiments") as mock_expire:
            main._expire_stale_experiments()

        mock_food.assert_called_once()
        assert mock_food.call_args[0][1] == expected_today

        mock_expire.assert_called_once()
        args, kwargs = mock_expire.call_args
        assert args[1] == expected_week
        assert args[2] == expected_today
        assert kwargs["tz_offset_minutes"] == tz_offset

    def test_defaults_to_utc_when_no_offset_is_configured(self, monkeypatch):
        expected_today = datetime.now(timezone.utc).date()

        monkeypatch.setattr(main, "SessionLocal", TestingSessionLocal)
        with patch("routers.correlations.check_food_avoidance_habits") as mock_food, \
             patch("routers.correlations.auto_expire_stale_experiments") as mock_expire:
            main._expire_stale_experiments()

        assert mock_food.call_args[0][1] == expected_today
        assert mock_expire.call_args[1]["tz_offset_minutes"] == 0

    def test_a_failure_is_caught_and_logged_not_raised(self, monkeypatch, capsys):
        monkeypatch.setattr(main, "SessionLocal", TestingSessionLocal)
        with patch("routers.correlations.check_food_avoidance_habits", side_effect=RuntimeError("boom")):
            main._expire_stale_experiments()  # must not raise

        assert "cleanup error" in capsys.readouterr().out
