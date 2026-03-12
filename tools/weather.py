"""
tools/weather.py — Current weather via Open-Meteo (free, no API key).

Hardcoded to Vancouver, BC (lat=49.2827, lon=-123.1207).
Open-Meteo returns WMO weather codes which we map to plain descriptions.
"""

from __future__ import annotations

import json
import urllib.request


_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=49.2827&longitude=-123.1207"
    "&current=temperature_2m,apparent_temperature,precipitation,weathercode,windspeed_10m"
    "&temperature_unit=celsius"
    "&windspeed_unit=kmh"
    "&timezone=America%2FVancouver"
)

# WMO weather interpretation codes → plain English
_WMO = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "icy fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "moderate showers",
    82: "heavy showers",
    85: "light snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def get_weather() -> str:
    """Fetch current Vancouver weather and return a spoken-friendly summary."""
    try:
        with urllib.request.urlopen(_URL, timeout=8) as resp:
            data = json.loads(resp.read())
        c = data["current"]
        temp = round(c["temperature_2m"])
        feels = round(c["apparent_temperature"])
        precip = c["precipitation"]
        wind = round(c["windspeed_10m"])
        desc = _WMO.get(int(c["weathercode"]), "unknown conditions")

        parts = [f"Vancouver is currently {temp} degrees Celsius with {desc}."]
        if abs(temp - feels) >= 3:
            parts.append(f"It feels like {feels}.")
        if precip > 0:
            parts.append(f"There's {precip} millimetres of precipitation.")
        if wind >= 20:
            parts.append(f"Wind is at {wind} kilometres per hour.")
        return " ".join(parts)
    except Exception as exc:
        return f"Sorry, I couldn't fetch the weather right now: {exc}"
