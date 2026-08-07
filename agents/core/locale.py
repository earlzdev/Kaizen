# =============================================================================
# Locale directory support — agents/core/locale.py
# =============================================================================
# WHAT: The generic machinery behind Agent Core's multi-language support: a
#       cached JSON reader, and a boot-time completeness check that an agent
#       calls once, before it loads anything, to fail loudly if the language
#       it was told to run in doesn't have every required file.
#
# WHY a hard boot-time check instead of per-file fallback-to-ru: a partially
#       translated language is worse silent than loud. If `KAYA_LANGUAGE=fr`
#       is set but only the soul got translated, falling back file-by-file
#       means the owner gets a French persona quoting Russian error messages
#       and English tracker templates — a broken experience that looks like a
#       bug in each individual place instead of what it actually is: one
#       incomplete language. Refusing to boot says so once, clearly, with the
#       exact missing paths.
#
# WHY this doesn't replace the individual loaders' own tolerance for a
#       CORRUPT (not missing) file — agents/core/cliches.py still logs and
#       continues with an empty map if a present file fails to parse, same as
#       before. This check only proves the files EXIST; a self-check gate
#       losing its cliché map to a JSON typo is a smaller, non-fatal problem,
#       and should stay non-fatal.
#
# HOW: each agent's locale data lives under `<package>/locales/<lang>/`. An
#       agent calls `require_language(lang, {root: (filenames...)})` for
#       every locale root it depends on (its own + agents/core's shared one)
#       before building anything, then loads files from those same roots.
# =============================================================================

import functools
import json
from pathlib import Path


class LanguageNotConfigured(RuntimeError):
    """Raised when a selected language is missing required locale files."""


@functools.lru_cache(maxsize=None)
def load_json(path: str) -> dict:
    """A locale JSON file, cached — these are read on every user-facing
    string lookup, not just at boot, so re-parsing per call would be wasted
    I/O on a long-running bot."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def missing_locale_files(language: str, roots: dict[Path, tuple[str, ...]]) -> list[str]:
    """Every required file, across every locale root, that the given
    language is missing. Empty list = the language is fully configured."""
    missing = []
    for root, filenames in roots.items():
        for name in filenames:
            path = root / language / name
            if not path.is_file():
                missing.append(str(path))
    return missing


def require_language(language: str, roots: dict[Path, tuple[str, ...]]) -> None:
    """Raise LanguageNotConfigured with the exact missing paths if `language`
    isn't fully set up across every given locale root."""
    missing = missing_locale_files(language, roots)
    if missing:
        raise LanguageNotConfigured(
            f"Language '{language}' is not fully configured — missing: " + ", ".join(missing)
        )
