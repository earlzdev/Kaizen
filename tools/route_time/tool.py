# =============================================================================
# route_time tool — tools/route_time/tool.py
# =============================================================================
# WHAT: Travel time (and distance) between two places, read live off Yandex
#       Maps' route page through the shared headless browser — car (with
#       current traffic), public transit, or walking.
#
# WHY scraping and not the Maps API: Yandex's routing APIs all require a key
#       and billing setup; the owner wants zero-registration tools. We already
#       scrape Yandex through the same browser for traffic_score, so the
#       pattern (and its fragility tradeoff) is established. If the page layout
#       changes, the regexes here are the only thing to fix.
#
# WHY it returns the top few options, not one number: the route panel lists
#       alternatives ("42 min", "55 min"...) and for transit they differ by
#       mode; the first duration on the page is Yandex's best suggestion, the
#       rest give the agent a realistic range to quote.
#
# WHY durations are reformatted instead of passed through as Yandex renders
#       them: Yandex's page always renders Russian units ("1 ч 42 мин")
#       regardless of query language — the regex below parses THAT format,
#       and `_format_duration` re-renders the parsed hours/minutes in
#       whichever language the caller asked for. The scrape stays
#       Russian-locale; only the final text output is bilingual.
#
# HOW: exports `TOOL`; the loader registers it.
# =============================================================================

import re
import urllib.parse

from tools.contract import ToolDef
from tools.shared.browser import BrowserService

_browser = BrowserService()

# Yandex route-type codes for the rtt URL parameter.
_MODES = {"car": "auto", "transit": "mt", "walk": "pd"}
_MODE_LABELS = {
    "en": {"car": "by car (with traffic)", "transit": "by public transit", "walk": "on foot"},
    "ru": {"car": "на машине (с учётом пробок)", "transit": "на общественном транспорте", "walk": "пешком"},
}

# Durations as Yandex renders them: «1 ч 42 мин», «42 мин», «5 ч».
_DURATION_RE = re.compile(r"(?:(\d+)\s*ч\.?\s*)?(\d+)\s*мин|(\d+)\s*ч\.?(?!\S)")
# Distances: «612 км», «4,5 км».
_DISTANCE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*км")


def _format_duration(hours: str | None, minutes: str | None, bare_hours: str | None, language: str) -> str:
    hour_word = "h" if language != "ru" else "ч"
    minute_word = "min" if language != "ru" else "мин"
    if bare_hours:
        return f"{bare_hours} {hour_word}"
    if hours:
        return f"{hours} {hour_word} {minutes} {minute_word}"
    return f"{minutes} {minute_word}"


def _durations(text: str, language: str, limit: int = 3) -> list[str]:
    """The first few route durations from the rendered page, formatted."""
    out = []
    for m in _DURATION_RE.finditer(text):
        hours, minutes, bare_hours = m.groups()
        out.append(_format_duration(hours, minutes, bare_hours, language))
        if len(out) >= limit:
            break
    return out


async def route_time(from_place: str, to_place: str, mode: str = "car", language: str = "en") -> str:
    if mode not in _MODES:
        return "Error: mode must be one of car|transit|walk."
    language = language if language in _MODE_LABELS else "en"
    rtext = urllib.parse.quote(f"{from_place}~{to_place}", safe="~")
    url = f"https://yandex.ru/maps/?rtext={rtext}&rtt={_MODES[mode]}"

    text = await _browser.render(url)
    if not text:
        return "Error: couldn't load the route (browser unavailable or Yandex Maps failed)."
    durations = _durations(text, language)
    if not durations:
        return (
            f"Error: Yandex Maps didn't show a route for '{from_place}' → '{to_place}'. "
            "Try more specific place names (city + street, or a landmark)."
        )
    distance = _DISTANCE_RE.search(text)
    mode_label = _MODE_LABELS[language][mode]
    parts = [
        f"{from_place} → {to_place} {mode_label}: {durations[0]}"
        + (f" ({distance.group(0)})" if distance else "")
    ]
    if len(durations) > 1:
        parts.append(f"Alternative routes: {', '.join(durations[1:])}")
    parts.append(url)
    return "\n".join(parts)


TOOL = ToolDef(
    name="route_time",
    description=(
        "Travel time between two places via Yandex Maps: by car (live traffic), "
        "public transit, or walking. ALWAYS use this when the owner asks how long "
        "it takes to get somewhere — never estimate from web search. If the "
        "starting point is not named, use the owner's home location from your "
        "context. Pass `language` matching the language you're currently replying in."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "from_place": {"type": "string", "description": "Start, e.g. 'Lisbon' or 'Москва, Тверская 1'"},
            "to_place": {"type": "string", "description": "Destination"},
            "mode": {"type": "string", "enum": ["car", "transit", "walk"], "description": "Default car"},
            "language": {"type": "string", "enum": ["en", "ru"], "description": "Output language (default en)"},
        },
        "required": ["from_place", "to_place"],
    },
    handler=route_time,
)
