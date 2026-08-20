"""
Tests for routers.withings.fetch_measurements and its three per-section helpers
(_fetch_steps, _fetch_body_measurements, _fetch_sleep) -- the pure, DB-free extraction
that both do_sync() and integrations.withings.WithingsProvider.sync() now share.

No real Withings API calls -- _withings_get is monkeypatched per test.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date
from unittest.mock import patch

import routers.withings as withings

_CREDS = {"access_token": "tok"}
_START = date(2026, 5, 1)
_END = date(2026, 5, 3)


class TestFetchSteps:
    def test_returns_measurements(self):
        body = {"activities": [
            {"date": "2026-05-01", "steps": 4000},
            {"date": "2026-05-02", "steps": 8000},
        ]}
        with patch.object(withings, "_withings_get", return_value=body):
            result = withings._fetch_steps(_CREDS, _START, _END)
        assert result == [
            withings.Measurement(date="2026-05-01", value=4000.0),
            withings.Measurement(date="2026-05-02", value=8000.0),
        ]

    def test_skips_entries_with_no_steps(self):
        body = {"activities": [{"date": "2026-05-01", "steps": None}]}
        with patch.object(withings, "_withings_get", return_value=body):
            result = withings._fetch_steps(_CREDS, _START, _END)
        assert result == []


class TestFetchBodyMeasurements:
    def test_maps_known_types_with_unit_scaling_and_rounding(self):
        body = {"measuregrps": [
            {"date": 1748736000, "measures": [
                {"type": 1, "value": 70123, "unit": -3},   # weight -> 70.123 -> round(2) = 70.12
                {"type": 6, "value": 2345, "unit": -2},    # fat_ratio -> 23.45
                {"type": 9, "value": 800, "unit": -1},     # bp_diastolic -> 80.0
                {"type": 10, "value": 1200, "unit": -1},   # bp_systolic -> 120.0
                {"type": 11, "value": 650, "unit": -1},    # heart_rate -> 65.0
                {"type": 54, "value": 970, "unit": -1},    # spo2 -> 97.0
            ]},
        ]}
        with patch.object(withings, "_withings_get", return_value=body):
            result = withings._fetch_body_measurements(_CREDS, _START, _END)
        assert result["weight"][0].value == 70.12
        assert result["fat_ratio"][0].value == 23.45
        assert result["bp_diastolic"][0].value == 80.0
        assert result["bp_systolic"][0].value == 120.0
        assert result["heart_rate"][0].value == 65.0
        assert result["spo2"][0].value == 97.0

    def test_ignores_unmapped_types(self):
        body = {"measuregrps": [
            {"date": 1748736000, "measures": [{"type": 999, "value": 1, "unit": 0}]},
        ]}
        with patch.object(withings, "_withings_get", return_value=body):
            result = withings._fetch_body_measurements(_CREDS, _START, _END)
        assert result == {}


class TestFetchSleep:
    def test_maps_all_four_fields(self):
        body = {"series": [
            {"date": "2026-05-01", "data": {
                "sleep_score": 82,
                "total_sleep_time": 27000.4,
                "deep_sleep_duration": 5400.6,
                "spo2_average": 96.7,
            }},
        ]}
        with patch.object(withings, "_withings_get", return_value=body):
            result = withings._fetch_sleep(_CREDS, _START, _END)
        assert result["sleep_score"][0].value == 82.0
        assert result["sleep_minutes"][0].value == 27000.0
        assert result["sleep_deep_minutes"][0].value == 5401.0
        assert result["spo2"][0].value == 96.7

    def test_skips_entries_with_no_date(self):
        body = {"series": [{"date": None, "data": {"sleep_score": 82}}]}
        with patch.object(withings, "_withings_get", return_value=body):
            result = withings._fetch_sleep(_CREDS, _START, _END)
        assert result == {}


class TestFetchMeasurements:
    def test_merges_all_three_sections(self):
        def fake_get(creds, path, params):
            if params["action"] == "getactivity":
                return {"activities": [{"date": "2026-05-01", "steps": 100}]}
            if params["action"] == "getmeas":
                return {"measuregrps": [{"date": 1748736000, "measures": [{"type": 1, "value": 700, "unit": -1}]}]}
            if params["action"] == "getsummary":
                return {"series": [{"date": "2026-05-01", "data": {"sleep_score": 90}}]}
            raise AssertionError(f"unexpected action {params['action']}")

        with patch.object(withings, "_withings_get", side_effect=fake_get):
            readings, errors = withings.fetch_measurements(_CREDS, _START, _END)

        assert errors == {}
        assert readings["steps"][0].value == 100.0
        assert readings["weight"][0].value == 70.0
        assert readings["sleep_score"][0].value == 90.0

    def test_one_section_failing_does_not_block_the_others(self):
        def fake_get(creds, path, params):
            if params["action"] == "getactivity":
                raise RuntimeError("activity boom")
            if params["action"] == "getmeas":
                return {"measuregrps": []}
            if params["action"] == "getsummary":
                return {"series": [{"date": "2026-05-01", "data": {"sleep_score": 90}}]}
            raise AssertionError

        with patch.object(withings, "_withings_get", side_effect=fake_get):
            readings, errors = withings.fetch_measurements(_CREDS, _START, _END)

        assert errors == {"activity": "activity boom"}
        assert "steps" not in readings
        assert readings["sleep_score"][0].value == 90.0

    def test_body_and_sleep_spo2_both_present_sleep_wins_on_upsert_order(self):
        """Both sections can report spo2 for the same date -- sleep's reading is appended
        after body's, so a caller upserting in order (as do_sync does) ends up with sleep's
        value, matching the pre-extraction behavior where the sleep block ran last."""
        def fake_get(creds, path, params):
            if params["action"] == "getactivity":
                return {"activities": []}
            if params["action"] == "getmeas":
                return {"measuregrps": [{"date": 1748736000, "measures": [{"type": 54, "value": 950, "unit": -1}]}]}
            if params["action"] == "getsummary":
                return {"series": [{"date": "2026-05-01", "data": {"spo2_average": 98.0}}]}
            raise AssertionError

        with patch.object(withings, "_withings_get", side_effect=fake_get):
            readings, errors = withings.fetch_measurements(_CREDS, _START, _END)

        assert [m.value for m in readings["spo2"]] == [95.0, 98.0]
