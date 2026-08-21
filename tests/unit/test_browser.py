# =============================================================================
# Unit tests — tools/shared/browser.py (challenge-page detection)
# =============================================================================
# WHAT: BrowserService._looks_like_challenge against fake Playwright pages —
#       the logic that decides whether a bot-wall interstitial (and therefore
#       CHALLENGE_BLOCKED) is showing, without launching a real browser.
# WHY: this is the part most likely to silently regress (a marker typo, a
#      swapped title/body check) and is cheap to fake — no Chromium needed.
# =============================================================================

from tools.shared.browser import BrowserService


class FakePage:
    """Stands in for a playwright Page: scripted title/body text."""

    def __init__(self, title="", body=""):
        self._title = title
        self._body = body

    async def title(self):
        return self._title

    async def inner_text(self, selector, timeout=None):
        return self._body


async def test_challenge_title_is_detected():
    page = FakePage(title="Just a moment...", body="")
    assert await BrowserService._looks_like_challenge(page) is True


async def test_challenge_body_is_detected():
    page = FakePage(title="Real Page", body="Пожалуйста, включите javascript и обновите страницу.")
    assert await BrowserService._looks_like_challenge(page) is True


async def test_ordinary_page_is_not_a_challenge():
    page = FakePage(title="Отрадный — квартиры от застройщика", body="Студии от 3.8 млн")
    assert await BrowserService._looks_like_challenge(page) is False


async def test_check_failure_is_not_a_challenge():
    class BrokenPage:
        async def title(self):
            raise RuntimeError("page closed")

    assert await BrowserService._looks_like_challenge(BrokenPage()) is False
