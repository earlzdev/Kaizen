# =============================================================================
# find_online tool — tools/find_online/tool.py
# =============================================================================
# WHAT: Searches the internet for a video / image / web page and returns
#       verified links. Wraps the ported WebSearchService (web_search.py).
#
# WHY it exists: a real example/link often answers better than words. The agent
#       picks the query + kind; this just fetches and returns links.
#
# HOW: exports `TOOL` (a ToolDef); the loader registers it; Brain advertises it.
# =============================================================================

from tools.contract import ToolDef
from tools.find_online.web_search import WebSearchService

_web = WebSearchService()

# Web results per search. Wide on purpose: the agent researches by scanning
# many snippets and then opening only the promising pages with read_page —
# 3 bare links (the old shape) gave it nothing to choose from.
_WEB_RESULTS = 8
# Media results stay small: each one is probed (alive check) and the owner
# only ever needs a couple of links/images.
_MEDIA_RESULTS = 3


def _format_result(i: int, r: dict) -> str:
    lines = [f"{i}. {r.get('title', '')}", f"   {r.get('url', '')}"]
    snippet = (r.get("snippet") or "").strip()
    if snippet:
        lines.append(f"   {snippet[:300]}")
    return "\n".join(lines)


async def find_online(query: str, kind: str = "web") -> str:
    if kind not in ("web", "video", "image"):
        return "Error: kind must be one of web|video|image."
    limit = _WEB_RESULTS if kind == "web" else _MEDIA_RESULTS
    results = await _web.search_media(query, kind, max_results=limit)
    if not results:
        return f"No {kind} results found for '{query}'."
    body = "\n".join(_format_result(i, r) for i, r in enumerate(results, 1))
    return (
        f"Top {kind} results for '{query}':\n{body}\n\n"
        "Snippets are previews, not proof — open the promising URLs with "
        "read_page before stating their contents as fact."
    )


TOOL = ToolDef(
    name="find_online",
    description=(
        "Search the internet: web pages (with snippets), a video, or an image. Call "
        "whenever the answer depends on facts you can't already prove, or when a real "
        "example/link would answer better than words. For research, call it several "
        "times with DIFFERENT query phrasings (and check review sites for products/"
        "places/services), then read the promising pages with read_page. Phrase each "
        "query in the language most likely to find good results (usually English)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string", "enum": ["web", "video", "image"]},
        },
        "required": ["query"],
    },
    handler=find_online,
    # Deliberately ONLY the concrete example: "search several times, then
    # read_page" is already said by this tool's description AND by the web
    # research protocol in the system prompt. The block ships every turn, so a
    # note that repeats what the model has already read twice is pure cost.
    usage=(
        'Vary the phrasing across calls, including the language: {"query": '
        '"best e-readers 2026 reviews"} then {"query": "сравнение электронных книг 2026"}.'
    ),
)
