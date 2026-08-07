# =============================================================================
# Unit tests — agents/kaya/connector.py (pure text helpers)
# =============================================================================
# WHAT: _strip_markdown (Telegram shows literal asterisks otherwise) and
#       _split_images (direct image URLs become real photos).
# WHY: these run on EVERY outgoing reply; a regression garbles every message.
# HOW: pure string in/out — no aiogram objects involved.
# =============================================================================

from agents.kaya.connector import _split_images, _strip_markdown


def test_markdown_link_becomes_title_and_url():
    assert _strip_markdown("see [docs](https://ex.com/a)") == "see docs — https://ex.com/a"


def test_bold_is_unwrapped():
    assert _strip_markdown("это **важно** тут") == "это важно тут"


def test_headings_are_flattened():
    assert _strip_markdown("## Заголовок\nтекст") == "Заголовок\nтекст"


def test_single_asterisks_are_left_alone():
    assert _strip_markdown("2 * 3 * 4") == "2 * 3 * 4"


def test_split_images_extracts_direct_urls():
    text, urls = _split_images("вот фото https://ex.com/cat.jpg смотри")
    assert urls == ["https://ex.com/cat.jpg"]
    assert "cat.jpg" not in text
    assert "вот фото" in text


def test_split_images_no_urls_returns_text_unchanged():
    assert _split_images("просто текст") == ("просто текст", [])


def test_split_images_caps_at_five():
    urls = " ".join(f"https://ex.com/{i}.png" for i in range(8))
    _, extracted = _split_images(urls)
    assert len(extracted) == 5


def test_split_images_query_string_url():
    _, urls = _split_images("https://ex.com/pic.webp?w=800&h=600")
    assert urls == ["https://ex.com/pic.webp?w=800&h=600"]
