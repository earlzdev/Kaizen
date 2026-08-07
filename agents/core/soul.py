# =============================================================================
# Soul loader — agents/core/soul.py
# =============================================================================
# WHAT: Loads an agent's `soul.md` — the Markdown file that IS the agent's
#       identity/persona and becomes the stable head of its system prompt.
#
# WHY soul.md is a file, not code: it is data, edited like prose, and it is what
#       makes two agents built from the SAME Agent Core feel different (Кая the
#       warm text companion vs Кузя the terse voice one). Keeping it out of code
#       means changing the persona never touches the loop.
#
# WHY it is the STABLE head of the prompt: everything per-message (recalled
#       memories, the clock) is appended AFTER the soul, so the expensive stable
#       prefix stays byte-identical between turns and the API prompt cache holds.
#
# HOW: `load_soul("agents/kaya/locales/ru/soul.md")` -> str. Raises FileNotFoundError with
#       a clear message if the path is wrong (an agent with no soul is a bug we
#       want to fail loudly at boot, not silently run faceless).
# =============================================================================

from pathlib import Path


def load_soul(path: str | Path) -> str:
    """Read an agent's soul.md and return its contents (stripped).

    Raises FileNotFoundError if the file is missing or empty — a faceless agent
    is almost always a misconfiguration, so we surface it at startup."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"soul.md not found at {p!s} — every agent needs a soul")
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise FileNotFoundError(f"soul.md at {p!s} is empty — every agent needs a soul")
    return text
