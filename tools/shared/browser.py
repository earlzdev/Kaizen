# =============================================================================
# Browser Service — app/services/browser.py
# =============================================================================
# WHAT: A headless Chromium (via Playwright) that renders JavaScript pages and
#       returns their text — so Kaya can read sites whose content only appears
#       after JS runs, and drive simple forms (type into fields, click).
#
# WHY this exists:
#   WebSearch/WebFetch fetch raw HTML; they do not run JavaScript. Modern sites
#   — Yandex Maps routes and distances, booking pages, most SPAs — render their
#   real content in the browser, so a raw fetch sees an empty shell. This is a
#   real browser, so it sees what a person sees.
#
# WHY the whole thing degrades gracefully:
#   Chromium is heavy and may be absent (a container built without it, or the
#   feature switched off). Playwright is imported lazily and every failure is
#   turned into a plain message, so a missing browser never crashes a chat turn
#   — Kaya just falls back to normal search.
#
# HOW it's used:
#   render(url) → visible text after the page settles. For sites where the data
#   is behind a form, `steps` types into fields and clicks before reading, e.g.
#   [{"fill": "input[name=from]", "text": "Lisbon"}, {"click": "button.search"}].
# =============================================================================

import asyncio
import logging
import time

from tools.config import settings

logger = logging.getLogger(__name__)

# How much rendered text to hand back — enough to answer from, small enough to
# stay well inside a prompt.
_MAX_TEXT = 12_000

# Hard ceiling on one render() call, REGARDLESS of settings.browser_timeout_seconds
# (which can be configured higher): the caller chain has its own limits — Brain
# gives browser-backed tools 25s (brain/modules.py _LONG_TIMEOUT_TOOLS) and the
# agent's own MCP call caps at 30s (agents/core/mcp_client.py) — so this needs
# margin under both, not just under itself. goto, the challenge check/retry, and
# steps all draw from this ONE shared budget instead of each getting a fresh
# full timeout, which is what let a single render() blow past those upstream
# deadlines before. Set below the Brain cap with room to spare, not right up
# against it: the challenge check draws from whatever's left in the budget
# too (see _looks_like_challenge) rather than adding fixed time of its own,
# but launch/teardown overhead is still outside the budget's control, so this
# stays well clear of the 25s Brain cap rather than right up against it.
_HARD_BUDGET_MS = 10_000

# Cheap bot-detection tells: vanilla Playwright leaves `navigator.webdriver`
# true and a headless-looking `navigator.plugins`, which is exactly what
# Cloudflare's JS challenge (and similar bot walls on sites like avito.ru,
# cian.ru, drive2.ru) checks before deciding to serve a "checking your
# browser" interstitial instead of the page. This patches the obvious tells
# before any site script runs. It is not a stealth arms-race guarantee —
# managed challenges that fingerprint deeper (TLS, canvas, timing) can still
# block it — but it clears the cheap checks that were blocking plain runs.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});
window.chrome = window.chrome || {runtime: {}};
"""

# Title/body snippets that mean "this is a challenge page, not the content" —
# checked (title first, then a body prefix) so a retry-after-wait can happen
# instead of returning the interstitial as if it were the real page.
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "attention required",
    "проверка браузера",
    "включите javascript и обновите",
    "подтвердите, что запросы отправляете вы",
)


# Returned by render() instead of None specifically when a bot-wall
# interstitial survived the retry — distinct from "unavailable"/"failed" so a
# caller that cares (read_page) can tell the model/owner what actually
# happened, instead of a generic failure message. NOT falsy — it's a non-empty
# string, so a plain `if not text` will NOT catch it. Every caller MUST
# compare with `text == CHALLENGE_BLOCKED` explicitly (see read_page,
# route_time, traffic_score for the pattern).
CHALLENGE_BLOCKED = "\x00CHALLENGE_BLOCKED\x00"


class BrowserService:
    """Renders JS pages with a headless Chromium, if one is available."""

    def __init__(self) -> None:
        self._enabled = settings.browser_enabled

    @property
    def available(self) -> bool:
        return self._enabled

    async def render(self, url: str, steps: list[dict] | None = None) -> str | None:
        """Open `url` in a real browser, optionally act on it, return its text.

        Returns None if the browser isn't available or the page failed — the
        caller then falls back to ordinary search instead of erroring. Returns
        CHALLENGE_BLOCKED (a non-empty string — NOT falsy, compare by
        equality) specifically when a bot-wall interstitial survived the
        retry, so a caller that wants to say why can (see read_page).
        """
        if not self._enabled:
            return None
        try:
            from playwright.async_api import (
                TimeoutError as PlaywrightTimeoutError,
                async_playwright,
            )
        except Exception:
            logger.warning("Playwright not installed — browser rendering unavailable")
            return None

        budget_ms = min(settings.browser_timeout_seconds * 1000, _HARD_BUDGET_MS)
        deadline = time.monotonic() + budget_ms / 1000

        def remaining_ms() -> int:
            # A small floor, not a large one: this used to be 1000ms, which
            # meant every remaining_ms()-timed call downstream still got a
            # full extra second AFTER the budget was exhausted — with two
            # challenge checks plus a wait plus a reload all drawing on it,
            # that alone added ~4-10s past the deadline. 300ms keeps each
            # step from being handed a timeout of literally 0 while no longer
            # meaningfully padding the total.
            return max(300, int((deadline - time.monotonic()) * 1000))

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                    timeout=remaining_ms(),
                )
                try:
                    page = await browser.new_page(
                        locale="ru-RU",
                        viewport={"width": 1920, "height": 1080},
                        user_agent=(
                            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
                        ),
                        extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
                    )
                    await page.add_init_script(_STEALTH_INIT_SCRIPT)
                    page.set_default_timeout(remaining_ms())
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=remaining_ms())
                    except PlaywrightTimeoutError:
                        # A bot-wall interstitial often keeps polling in the
                        # background, so "networkidle" never fires even though
                        # the page (challenge or real content) did load — check
                        # what's there instead of giving up outright.
                        logger.debug("goto networkidle timeout for %s — checking loaded content anyway", url)

                    # A bot-wall interstitial resolves itself client-side in a
                    # few seconds on many sites (Cloudflare's "Just a moment").
                    # One bounded wait-and-reload gives that a chance instead
                    # of handing back the interstitial as if it were content.
                    # Draws from the same shared budget as everything else in
                    # this call — no fresh full timeout on top.
                    # set_default_timeout is a snapshot, not live — re-set it
                    # after every wait so calls with no explicit `timeout=`
                    # (page.title(), which the Python API doesn't accept one
                    # for) can't fall back to the stale, much-larger value
                    # from before goto and run past the shared deadline.
                    page.set_default_timeout(remaining_ms())
                    if await self._looks_like_challenge(page, remaining_ms()):
                        await page.wait_for_timeout(min(3000, remaining_ms()))
                        try:
                            await page.reload(wait_until="networkidle", timeout=remaining_ms())
                        except PlaywrightTimeoutError:
                            pass
                        page.set_default_timeout(remaining_ms())
                        if await self._looks_like_challenge(page, remaining_ms()):
                            logger.info("Still a challenge page after retry: %s", url)
                            return CHALLENGE_BLOCKED

                    for step in steps or []:
                        if "fill" in step:
                            await page.fill(step["fill"], step.get("text", ""))
                        elif "click" in step:
                            await page.click(step["click"])
                        elif "press" in step:
                            await page.keyboard.press(step["press"])
                    if steps:
                        # Let results load after the interactions.
                        await page.wait_for_load_state("networkidle", timeout=remaining_ms())

                    text = await page.inner_text("body", timeout=remaining_ms())
                finally:
                    await browser.close()
        except Exception as e:
            logger.warning("Browser render failed for %s: %s", url, e)
            return None

        # Collapse the whitespace the way the web-search extractor does.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[:_MAX_TEXT]

    @staticmethod
    async def _looks_like_challenge(page, budget_ms: int = 2000) -> bool:
        # Capped by whatever's left of the caller's own render() budget (2s
        # only as a fallback default) — this only reads a title and a body
        # prefix, which is fast on any real page, but the two calls here
        # still draw from the SAME shared budget as everything else in
        # render(), not fixed time on top of it. page.title() takes no
        # `timeout=` in the Python API, so it's wrapped instead of relying on
        # the page's (possibly stale) default timeout.
        check_ms = min(2000, budget_ms)
        try:
            title = (await asyncio.wait_for(page.title(), timeout=check_ms / 1000)).lower()
            if any(marker in title for marker in _CHALLENGE_MARKERS):
                return True
            body = (await page.inner_text("body", timeout=check_ms))[:500].lower()
        except Exception as e:
            logger.debug("Challenge-page check failed for a page: %s", e)
            return False
        return any(marker in body for marker in _CHALLENGE_MARKERS)
