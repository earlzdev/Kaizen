# =============================================================================
# weather tool — tools/weather/tool.py
# =============================================================================
# WHAT: Current weather + daily forecast (up to 7 days) for any place, via the
#       free Open-Meteo API (no key, no registration): geocode the place name,
#       then fetch the forecast for its coordinates.
#
# WHY a dedicated tool instead of web search: before this, "what's the
#       weather" went through find_online — the agent read weather out of search
#       snippets and random news sites (slow, stale, and it dragged the whole
#       research machinery — statuses, fact-check — into a trivial question).
#       An API answer is fresh, deterministic, and takes about a second.
#
# WHY the tool doesn't read the owner's profile: tools are stateless and have
#       no Brain access (service isolation) — the AGENT knows the owner's home
#       city from its runtime context and passes it as `location`.
#
# WHY a `language` argument instead of reading agent config: tools have no
#       Brain access and don't know which agent (or which KAYA_LANGUAGE) is
#       calling — the model itself already knows what language it's replying
#       in, so it's cheaper to just have it pass that along than to plumb
#       agent identity through the gRPC contract for this alone.
#
# HOW: exports `TOOL`; the loader registers it.
# =============================================================================

import httpx

from tools.contract import ToolDef

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_MAX_DAYS = 7

# WMO weather codes (Open-Meteo's `weathercode`) -> short description, per
# language. Grouped by tens; good enough for a chat answer, not a
# meteorology exam.
_WMO = {
    "en": {
        0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "rime fog",
        51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
        56: "freezing drizzle", 57: "heavy freezing drizzle",
        61: "light rain", 63: "rain", 65: "heavy rain",
        66: "freezing rain", 67: "heavy freezing rain",
        71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
        80: "light showers", 81: "showers", 82: "heavy showers",
        85: "snow showers", 86: "heavy snow showers",
        95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
    },
    "ru": {
        0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно",
        45: "туман", 48: "изморозь",
        51: "лёгкая морось", 53: "морось", 55: "сильная морось",
        56: "ледяная морось", 57: "сильная ледяная морось",
        61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
        66: "ледяной дождь", 67: "сильный ледяной дождь",
        71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
        80: "небольшой ливень", 81: "ливень", 82: "сильный ливень",
        85: "снегопад", 86: "сильный снегопад",
        95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
    },
}


def _describe(code: int | None, language: str) -> str:
    # `code or -1` would look like the natural guard for "no code", but WMO
    # code 0 IS a real value ("clear sky") and is falsy in Python — that
    # used to silently swallow every genuinely clear-sky reading. Check for
    # None explicitly instead.
    return _WMO.get(language, _WMO["en"]).get(code if code is not None else -1, "")


def _num(value) -> str:
    """Round a numeric field, tolerating the API's occasional nulls (far
    forecast days) — one missing number must not kill the whole answer."""
    return "?" if value is None else str(round(value))


async def weather(location: str, days: int = 1, language: str = "en") -> str:
    days = max(1, min(int(days), _MAX_DAYS))
    language = language if language in _WMO else "en"
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1) Place name -> coordinates. Passing the same language resolves
        # the place name in it too (e.g. language=ru resolves "Уфа" and
        # returns "Уфа", not "Ufa") — and either language accepts input in
        # the other script anyway (Open-Meteo's geocoder isn't picky).
        geo = await client.get(
            _GEOCODE_URL, params={"name": location, "count": 1, "language": language}
        )
        geo.raise_for_status()
        places = (geo.json() or {}).get("results") or []
        if not places:
            return f"Error: couldn't find a place named '{location}'."
        place = places[0]
        where = ", ".join(
            p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p
        )

        # 2) Forecast for those coordinates. timezone=auto -> dates/times are
        # LOCAL to the place, which is what a human means by "завтра".
        fc = await client.get(
            _FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current_weather": "true",
                "daily": "weathercode,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max,wind_speed_10m_max",
                "wind_speed_unit": "ms",
                "timezone": "auto",
                "forecast_days": days,
            },
        )
        fc.raise_for_status()
        data = fc.json() or {}

    lines = [f"Weather for {where}:"]
    current = data.get("current_weather") or {}
    if current:
        lines.append(
            f"Now: {_num(current.get('temperature'))}°C, "
            f"{_describe(current.get('weathercode'), language)}, "
            f"wind {_num(current.get('windspeed'))} m/s"
        )
    daily = data.get("daily") or {}
    for i, day in enumerate(daily.get("time") or []):
        lines.append(
            f"{day}: {_num(daily['temperature_2m_min'][i])}…"
            f"{_num(daily['temperature_2m_max'][i])}°C, "
            f"{_describe(daily['weathercode'][i], language)}, "
            f"precip {daily['precipitation_probability_max'][i] or 0}%, "
            f"wind up to {_num(daily['wind_speed_10m_max'][i])} m/s"
        )
    return "\n".join(lines)


TOOL = ToolDef(
    name="weather",
    description=(
        "Current weather and daily forecast (1-7 days) for a city or place, from "
        "the Open-Meteo API — fresh and instant. ALWAYS use this for any weather "
        "question instead of web search. If the owner doesn't name a place, use "
        "their home location from your context. For 'through the end of the week' "
        "pass enough days to reach Sunday. Pass `language` matching the language "
        "you're currently replying in, so the place name and conditions come back "
        "in it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City/place name, e.g. 'Lisbon' or 'Уфа'"},
            "days": {"type": "integer", "description": "Forecast days ahead, 1-7 (default 1)"},
            "language": {"type": "string", "enum": ["en", "ru"], "description": "Output language (default en)"},
        },
        "required": ["location"],
    },
    handler=weather,
)
