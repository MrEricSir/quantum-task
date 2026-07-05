"""
Unit tests for weather.py — fetch_weather and WMO code mappings.
"""

from unittest.mock import patch, MagicMock
import pytest

from weather import fetch_weather


def make_api_response(weathercode=0, windspeed=10.0, high=72, low=55):
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "current_weather": {
            "weathercode": weathercode,
            "windspeed": windspeed,
        },
        "daily": {
            "temperature_2m_max": [high],
            "temperature_2m_min": [low],
        },
    }
    return mock


@patch("weather.http_requests.get")
class TestFetchWeather:
    def test_clear_skies(self, mock_get):
        mock_get.return_value = make_api_response(weathercode=0)
        result = fetch_weather(40.7, -74.0)

        assert result is not None
        assert result["description"] == "clear skies"
        assert result["umbrella"] is False
        assert result["snow"] is False
        assert result["windy"] is False

    def test_rain_sets_umbrella(self, mock_get):
        mock_get.return_value = make_api_response(weathercode=63)
        result = fetch_weather(40.7, -74.0)

        assert result["umbrella"] is True
        assert result["description"] == "rain"

    def test_snow_sets_snow_flag(self, mock_get):
        mock_get.return_value = make_api_response(weathercode=73)
        result = fetch_weather(40.7, -74.0)

        assert result["snow"] is True
        assert result["description"] == "snow"

    def test_high_wind_sets_windy(self, mock_get):
        mock_get.return_value = make_api_response(windspeed=30.0)
        result = fetch_weather(40.7, -74.0)

        assert result["windy"] is True
        assert "💨" in result["emojis"]

    def test_cold_temp_sets_cold_flag(self, mock_get):
        mock_get.return_value = make_api_response(high=40, low=28)
        result = fetch_weather(40.7, -74.0)

        assert result["cold"] is True
        assert result["high"] == 40
        assert result["low"] == 28

    def test_warm_temp_not_cold(self, mock_get):
        mock_get.return_value = make_api_response(high=75, low=55)
        result = fetch_weather(40.7, -74.0)

        assert result["cold"] is False

    def test_thunderstorm_umbrella(self, mock_get):
        mock_get.return_value = make_api_response(weathercode=95)
        result = fetch_weather(40.7, -74.0)

        assert result["umbrella"] is True
        assert result["description"] == "thunderstorms"

    def test_unknown_wmo_code_falls_back(self, mock_get):
        mock_get.return_value = make_api_response(weathercode=999)
        result = fetch_weather(40.7, -74.0)

        assert result is not None
        assert result["description"] == "mixed conditions"
        assert result["emojis"] == "🌡️"

    def test_network_error_returns_none(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        result = fetch_weather(40.7, -74.0)

        assert result is None

    def test_passes_coordinates_to_api(self, mock_get):
        mock_get.return_value = make_api_response()
        fetch_weather(51.5, -0.12)

        call_kwargs = mock_get.call_args
        params = call_kwargs[1]["params"]
        assert params["latitude"] == 51.5
        assert params["longitude"] == -0.12

    def test_drizzle_sets_umbrella(self, mock_get):
        mock_get.return_value = make_api_response(weathercode=51)
        result = fetch_weather(40.7, -74.0)

        assert result["umbrella"] is True
        assert result["snow"] is False

    def test_fog_no_umbrella_no_snow(self, mock_get):
        mock_get.return_value = make_api_response(weathercode=45)
        result = fetch_weather(40.7, -74.0)

        assert result["umbrella"] is False
        assert result["snow"] is False
        assert result["description"] == "foggy"
