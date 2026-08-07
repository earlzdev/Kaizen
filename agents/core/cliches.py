# =============================================================================
# Shared cliché map — agents/core/cliches.py
# =============================================================================
# WHAT: Loads the shared AI-cliché map (cliches.json — bad phrasings -> live
#       replacements) and renders it into a prompt block any agent appends to
#       its system prompt below the soul.
#
# WHY it lives in Agent Core, as data + tiny renderer: the clichés are not one
#       agent's problem — every LLM agent produces the same «короче, вот что
#       по…» / «дай знать, если что» register, so the map is shared (Кая now,
#       Кузя when he moves onto this stack). JSON (not markdown) so an agent
#       could later subset/extend it programmatically; the renderer keeps the
#       prompt shape in ONE place instead of each soul re-formatting it.
#
# WHY the self-check sees it automatically: the gate turn runs with the same
#       system prompt as the main turn, and its style clause (prompts.py)
#       explicitly tells the checker to enforce the cliché map appended below
#       the soul.
#
# WHY it lives under locales/<lang>/: one map per language, same shape — see
#       agents/core/locale.py for the boot-time completeness check that makes
#       sure a configured language actually has a cliches.json here before an
#       agent starts.
#
# HOW: `from agents.core.cliches import load_cliches` ->
#      `soul + "\n\n" + load_cliches(path_for_language(lang))`.
# =============================================================================

import logging
from pathlib import Path

from agents.core.locale import load_json

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).with_name("locales")

# Fallback header if a map predates the "header" key. Language-neutral on
# purpose — every current map sets its own "header", so this is only a
# last-resort default, never something that should leak the wrong language.
_HEADER = "# Cliché list"


def path_for_language(language: str) -> Path:
    """The shared cliché map for a given language code."""
    return LOCALES_DIR / language / "cliches.json"


def load_cliches(path: Path | None = None) -> str:
    """The cliché map rendered as a system-prompt block ('' if unavailable —
    a present-but-CORRUPT map must never prevent an agent from booting; a
    missing one is instead caught earlier by locale.require_language)."""
    path = path or path_for_language("en")
    try:
        data = load_json(str(path))
    except (OSError, ValueError):
        # ValueError covers both JSONDecodeError and UnicodeDecodeError.
        logger.exception("Cliché map %s unreadable — continuing without it", path)
        return ""

    lines = [data.get("header", _HEADER), "", data.get("preamble", "")]
    for section in data.get("sections", []):
        lines.append("")
        lines.append(f"## {section.get('title', '')}")
        for rule in section.get("rules", []):
            bad = ", ".join(f"«{b}»" for b in rule.get("bad", []))
            # «say» = example replacements (quoted, speakable);
            # «do» = an instruction about the phrase (bracketed, never spoken).
            parts = []
            if rule.get("say"):
                parts.append(" / ".join(f"«{s}»" for s in rule["say"]))
            if rule.get("do"):
                parts.append(f"[{rule['do']}]")
            lines.append(f"- {bad} → {' '.join(parts) or '[remove]'}")
    return "\n".join(lines).strip()
