# =============================================================================
# Кая user-facing strings — agents/kaya/strings.py
# =============================================================================
# WHAT: Every literal string Кая's connector/delivery code sends straight to
#       the owner (or injects as a labeled note the model reads), loaded from
#       agents/kaya/locales/<lang>/strings.json.
#
# WHY these live here and not in soul.md: the soul is what the MODEL writes
#       with; this is text the CONNECTOR emits directly (errors before the
#       agent even sees the message, the "typing…" status line, the
#       reminder/tracker delivery prefixes) — none of it goes through Claude.
#
# WHY no per-key fallback to ru: agents/kaya/main.py calls
#       agents.core.locale.require_language at boot, which refuses to start
#       unless the configured language's strings.json exists and (by
#       construction, since it's checked as one file) is complete. A KeyError
#       here means the file itself is missing an entry it's supposed to have
#       — a bug in that language's file, not something to paper over at
#       runtime by mixing in Russian mid-conversation.
#
# HOW: `from agents.kaya.strings import t` -> `t(settings.kaya_language,
#      "voice_not_configured")`.
# =============================================================================

from pathlib import Path

from agents.core.locale import load_json

LOCALES_DIR = Path(__file__).with_name("locales")


def _strings(language: str) -> dict:
    return load_json(str(LOCALES_DIR / language / "strings.json"))


def t(language: str, key: str, **kwargs) -> str:
    """A user-facing string by key, in the given language, .format()ed with
    kwargs."""
    template = _strings(language)[key]
    return template.format(**kwargs) if kwargs else template


def status_line(language: str, tool: str, detail: str) -> str | None:
    """A short progress line for a phase worth showing the owner — or None
    for internal tools (memory, reminders) that would just be noise.
    `detail` doubles as the host for read_page/WebFetch (the caller strips
    the URL down to a bare host before calling)."""
    if tool in ("find_online", "WebSearch"):
        return t(language, "status_searching", detail=detail) if detail else t(language, "status_searching_generic")
    if tool in ("read_page", "WebFetch"):
        return t(language, "status_reading", host=detail) if detail else t(language, "status_reading_generic")
    if tool == "self_check":
        return t(language, "status_self_check")
    return None
