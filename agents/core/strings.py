# =============================================================================
# Agent Core user-facing strings — agents/core/strings.py
# =============================================================================
# WHAT: The handful of literal strings Agent Core ever sends straight to a
#       human (not to the model) — currently just the CLI backend's
#       quota-exhausted message. Everything else in agents/core/prompts.py is
#       model-facing instruction text, which Claude reads fine in English
#       regardless of what language the conversation is in.
#
# WHY separate from prompts.py: prompts.py is text APPENDED to a system
#       prompt or injected as a turn; this is text a connector hands straight
#       to the user, so it has to match whatever language THAT agent is
#       configured for (agents/kaya/config.py's kaya_language).
#
# WHY it lives under locales/<lang>/strings.json rather than a Python dict:
#       same reasoning as cliches.py — one file per language, and
#       agents/core/locale.py's boot-time check can verify it exists before
#       an agent starts, instead of a typo'd language code silently falling
#       back mid-conversation.
#
# HOW: `from agents.core.strings import quota_exhausted` ->
#      `quota_exhausted(language, reset_at)`.
# =============================================================================

from pathlib import Path

from agents.core.locale import load_json

LOCALES_DIR = Path(__file__).with_name("locales")


def quota_exhausted(language: str, reset_at: str | None) -> str:
    """The message shown to the owner when the CLI backend's Max subscription
    quota is spent."""
    data = load_json(str(LOCALES_DIR / language / "strings.json"))
    when = data["quota_reset_at"].format(reset_at=reset_at) if reset_at else ""
    return data["quota_exhausted"].format(when=when)
