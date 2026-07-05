import requests as http_requests

_WMO_EMOJI = {
    0: "☀️",
    1: "🌤️",  2: "⛅",  3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌧️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "🌨️",
    80: "🌦️", 81: "🌧️", 82: "🌧️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}
_WMO_DESC = {
    0: "clear skies",
    1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "light showers", 81: "showers", 82: "heavy showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "severe thunderstorms",
}

_RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 98, 99}
_SNOW_CODES = {71, 73, 75, 77, 85, 86}


def fetch_weather(lat: float, lon: float) -> dict | None:
    try:
        r = http_requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current_weather": "true",
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        cw          = data["current_weather"]
        high        = round(data["daily"]["temperature_2m_max"][0])
        low         = round(data["daily"]["temperature_2m_min"][0])
        code        = int(cw.get("weathercode", 0))
        windy       = float(cw.get("windspeed", 0)) > 25
        emoji       = _WMO_EMOJI.get(code, "🌡️")
        if windy:
            emoji += "💨"
        description = _WMO_DESC.get(code, "mixed conditions")
        return {
            "emojis": emoji, "high": high, "low": low,
            "description": description, "windy": windy,
            "umbrella": code in _RAIN_CODES,
            "snow": code in _SNOW_CODES,
            "cold": high < 45,
        }
    except Exception:
        return None
