# =============================================================================
# Web Search Service — app/services/web_search.py
# =============================================================================
# WHAT: Searches the internet and extracts text content from web pages.
#
# WHY this exists:
#   The bot originally required users to upload PDFs/text manually. This
#   service lets the bot find learning materials automatically when the user
#   wants to study a topic they don't have documents for.
#
# HOW it works:
#   1. Uses duckduckgo-search for free, no-API-key web searches
#   2. Fetches full page content with httpx
#   3. Extracts article text with BeautifulSoup (strips nav, ads, etc.)
#   4. Returns clean text ready for chunking and embedding
# =============================================================================

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS  # maintained successor of duckduckgo-search

logger = logging.getLogger(__name__)

# Tags that typically contain main article content
CONTENT_TAGS = ["article", "main", "section"]
# Tags to strip (navigation, ads, footers, etc.)
STRIP_TAGS = ["nav", "header", "footer", "aside", "script", "style", "noscript", "iframe"]


def _parse_page_text(html: str) -> str | None:
    """Strip a page down to its readable text. SYNCHRONOUS and CPU-bound.

    WHY it is a module-level function and not inlined in the coroutine:
    BeautifulSoup's html.parser is pure Python and takes hundreds of
    milliseconds — sometimes seconds — on a real-world page. Run on the event
    loop that is tens of seconds of freeze per deep-research harvest step
    (which fetches a dozen pages at once, for hours), and during that freeze
    aiogram cannot deliver a single message. Pulling the parse out lets the
    caller push it onto a worker thread with asyncio.to_thread, the same
    pattern already used for the ddgs search and the embedder.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements
    for tag in soup.find_all(STRIP_TAGS):
        tag.decompose()

    # Try to find the main content area
    content = None
    for tag_name in CONTENT_TAGS:
        content = soup.find(tag_name)
        if content:
            break

    # Fall back to body if no content area found
    if not content:
        content = soup.find("body")

    if not content:
        return None

    # Extract text, collapsing whitespace
    text = content.get_text(separator="\n", strip=True)

    # Clean up excessive blank lines
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    clean_text = "\n".join(lines)

    # Cap at ~10k characters (~2500 tokens) per page to avoid
    # ingesting massive pages
    return clean_text[:10000]


class WebSearchService:
    """Searches the web and extracts text content for the RAG pipeline."""

    async def search_and_extract(
        self,
        query: str,
        max_results: int = 3,
    ) -> list[str]:
        """Search DuckDuckGo and extract text from top results.

        Returns a list of extracted text strings (one per page).
        Empty strings and failed fetches are filtered out.
        """
        # DuckDuckGo search (sync library, but fast enough for our use case)
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
        except Exception:
            logger.exception("DuckDuckGo search failed for: %s", query)
            return []

        if not results:
            return []

        # Fetch and extract text from each result
        texts = []
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for result in results:
                url = result.get("href") or result.get("link")
                if not url:
                    continue
                text = await self._extract_page_text(client, url)
                if text and len(text) > 200:  # skip tiny pages
                    texts.append(text)

        return texts

    async def search_media(self, query: str, kind: str, max_results: int = 3) -> list[dict]:
        """Search the internet for videos, images, or web pages — and verify
        the links actually work before returning them.

        Returns a list of {'title': ..., 'url': ...} dicts (empty on failure).
        This powers the agent's generic "find me an example online" ability —
        the agent picks the query and the kind; we just fetch.

        WHY validation: search indexes are full of dead pages and (for
        images) hotlink-protected URLs that 403 when Telegram tries to
        preview them. We over-fetch candidates, probe them concurrently,
        and return only the ones that respond — a broken link never
        reaches the user.
        """
        # The DDGS client is synchronous — run it in a thread so the bot's
        # event loop (other users' messages, reminder delivery) isn't blocked.
        candidates = await asyncio.to_thread(
            self._search_media_sync, query, kind, max_results * 3
        )
        alive = await self._filter_alive(candidates, kind)
        return alive[:max_results]

    def _search_media_sync(self, query: str, kind: str, max_results: int) -> list[dict]:
        """The raw (blocking) search: normalize ddgs result keys per kind."""
        try:
            with DDGS() as ddgs:
                if kind == "video":
                    # ddgs's dedicated video backend is unreliable, so we
                    # search the web scoped to YouTube instead — same result
                    # (watch links Telegram can preview), far more robust.
                    raw = list(ddgs.text(f"{query} site:youtube.com", max_results=max_results))
                    return [
                        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                        for r in raw
                        if r.get("href")
                    ]
                if kind == "image":
                    raw = list(ddgs.images(query, max_results=max_results))
                    # 'image' is the direct image URL; 'url' is the source page
                    return [
                        {"title": r.get("title", ""), "url": r.get("image") or r.get("url", "")}
                        for r in raw
                        if r.get("image") or r.get("url")
                    ]
                # default: regular web results. The snippet ("body") matters:
                # it's what lets the agent decide which results deserve a full
                # read_page instead of opening pages blindly.
                raw = list(ddgs.text(query, max_results=max_results))
                return [
                    {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                    for r in raw
                    if r.get("href")
                ]
        except Exception:
            logger.exception("Media search failed for %r (%s)", query, kind)
            return []

    async def _filter_alive(self, candidates: list[dict], kind: str) -> list[dict]:
        """Probe candidate URLs concurrently; keep only the ones that respond.

        For images we also require an image/* content-type — a 200 that
        returns an HTML error page would still break the Telegram preview.
        YouTube links skip the content-type check (they're HTML pages that
        Telegram previews via its own player)."""
        if not candidates:
            return []

        async def probe(item: dict) -> dict | None:
            try:
                async with httpx.AsyncClient(
                    timeout=5.0, follow_redirects=True
                ) as client:
                    # stream() reads headers without downloading the body
                    async with client.stream(
                        "GET",
                        item["url"],
                        headers={"User-Agent": "Mozilla/5.0 (compatible; LearnBot/1.0)"},
                    ) as resp:
                        if resp.status_code >= 400:
                            return None
                        if kind == "image":
                            ctype = resp.headers.get("content-type", "")
                            if not ctype.startswith("image/"):
                                return None
                        return item
            except Exception:
                return None  # timeouts, DNS failures, TLS errors → dead link

        probed = await asyncio.gather(*(probe(c) for c in candidates))
        return [item for item in probed if item is not None]

    async def _extract_page_text(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Fetch a URL and extract its main text content.

        Uses BeautifulSoup to strip navigation, scripts, and other
        non-content elements, then extracts readable text.
        """
        try:
            response = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; LearnBot/1.0)"},
            )
            response.raise_for_status()

            # Only process HTML
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                return None

            # Parsing is CPU-bound and slow enough to stall the whole bot if
            # done here — a deep research harvests a dozen pages at a time and
            # keeps doing it for hours. Off the loop it goes.
            return await asyncio.to_thread(_parse_page_text, response.text)

        except Exception:
            logger.debug("Failed to extract text from %s", url)
            return None
