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
#   [{"fill": "input[name=from]", "text": "Москва"}, {"click": "button.search"}].
# =============================================================================

import logging

from tools.config import settings

logger = logging.getLogger(__name__)

# How much rendered text to hand back — enough to answer from, small enough to
# stay well inside a prompt.
_MAX_TEXT = 12_000


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
        caller then falls back to ordinary search instead of erroring.
        """
        if not self._enabled:
            return None
        try:
            from playwright.async_api import async_playwright
        except Exception:
            logger.warning("Playwright not installed — browser rendering unavailable")
            return None

        timeout_ms = settings.browser_timeout_seconds * 1000
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                try:
                    page = await browser.new_page(
                        locale="ru-RU",
                        user_agent=(
                            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
                        ),
                    )
                    page.set_default_timeout(timeout_ms)
                    await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

                    for step in steps or []:
                        if "fill" in step:
                            await page.fill(step["fill"], step.get("text", ""))
                        elif "click" in step:
                            await page.click(step["click"])
                        elif "press" in step:
                            await page.keyboard.press(step["press"])
                    if steps:
                        # Let results load after the interactions.
                        await page.wait_for_load_state("networkidle", timeout=timeout_ms)

                    text = await page.inner_text("body")
                finally:
                    await browser.close()
        except Exception as e:
            logger.warning("Browser render failed for %s: %s", url, e)
            return None

        # Collapse the whitespace the way the web-search extractor does.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[:_MAX_TEXT]
