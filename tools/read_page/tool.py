# =============================================================================
# read_page tool — tools/read_page/tool.py
# =============================================================================
# WHAT: Opens a URL in a real (JS-rendering) headless browser and returns its
#       visible text — for pages whose content only appears after JavaScript
#       runs. Uses the shared BrowserService (tools/shared/browser.py).
#
# WHY the browser is shared (tools/shared): the headless Chromium is heavy and
#       also used by traffic_score, so it lives in one place rather than per-tool.
#
# HOW: exports `TOOL`; the loader registers it.
# =============================================================================

from tools.contract import ToolDef
from tools.shared.browser import CHALLENGE_BLOCKED, BrowserService

# Cap the text handed back so a tool result stays prompt-sized.
_MAX_TEXT = 12_000

_browser = BrowserService()


async def read_page(url: str) -> str:
    text = await _browser.render(url)
    if text == CHALLENGE_BLOCKED:
        return "Error: this site blocked the request with a bot-check page — say so plainly, don't invent an answer."
    if not text:
        return "Error: couldn't render that page (browser unavailable or the page failed)."
    return text[:_MAX_TEXT]


TOOL = ToolDef(
    name="read_page",
    description=(
        "Open a specific URL in a real (JS-rendering) browser and return its visible "
        "text. Call to read a page whose content only appears after JavaScript runs, "
        "or when a raw fetch returned an empty shell."
    ),
    input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    handler=read_page,
)
