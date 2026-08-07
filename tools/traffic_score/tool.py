# =============================================================================
# traffic_score tool — tools/traffic_score/tool.py
# =============================================================================
# WHAT: Returns a city's current traffic congestion score (0-10). Wraps the
#       ported TrafficService (traffic.py), which reads it via the shared
#       headless browser (tools/shared/browser.py).
#
# HOW: exports `TOOL`; the loader registers it.
# =============================================================================

from tools.contract import ToolDef
from tools.shared.browser import BrowserService
from tools.traffic_score.traffic import TrafficService

_traffic = TrafficService(BrowserService())


async def traffic_score(city: str) -> str:
    if not _traffic.known(city):
        return f"Error: traffic isn't mapped for '{city}'."
    result = await _traffic.city_score(city)
    if result is None:
        return f"Error: couldn't read the traffic score for '{city}' right now."
    score, label, url = result
    return f"{city}: traffic {score}/10 ({label}). {url}"


TOOL = ToolDef(
    name="traffic_score",
    description=(
        "Get a city's current traffic congestion score (0-10). Call when the owner "
        "asks how bad traffic is somewhere."
    ),
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    handler=traffic_score,
)
