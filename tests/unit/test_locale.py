# =============================================================================
# Unit tests — agents/core/locale.py + agents/core/cliches.py
# =============================================================================
# WHAT: The two load-bearing promises of the multi-language refactor:
#       require_language() must name every missing file for a half-configured
#       language (the whole point is failing loudly, at boot, with specifics);
#       load_cliches() must degrade to "" on a CORRUPT map instead of crashing
#       (a present-but-broken map is a smaller problem than a missing one).
# WHY: nothing else in the suite touches agents/core/locale.py directly.
# HOW: a fabricated tmp_path locale tree, not the repo's real locales/.
# =============================================================================

import json

from agents.core.cliches import load_cliches
from agents.core.locale import missing_locale_files, require_language


def test_missing_locale_files_lists_every_missing_path(tmp_path):
    root = tmp_path / "locales"
    (root / "en").mkdir(parents=True)
    (root / "en" / "soul.md").write_text("hi")
    # strings.json deliberately absent.

    missing = missing_locale_files("en", {root: ("soul.md", "strings.json")})

    assert missing == [str(root / "en" / "strings.json")]


def test_missing_locale_files_empty_when_language_complete(tmp_path):
    root = tmp_path / "locales"
    (root / "en").mkdir(parents=True)
    (root / "en" / "soul.md").write_text("hi")
    (root / "en" / "strings.json").write_text("{}")

    assert missing_locale_files("en", {root: ("soul.md", "strings.json")}) == []


def test_require_language_raises_with_missing_paths(tmp_path):
    root = tmp_path / "locales"
    (root / "fr").mkdir(parents=True)
    # No files at all for "fr".

    try:
        require_language("fr", {root: ("soul.md",)})
    except Exception as e:
        assert str(root / "fr" / "soul.md") in str(e)
    else:
        raise AssertionError("require_language should have raised")


def test_load_cliches_on_corrupt_json_returns_empty_not_raises(tmp_path):
    bad = tmp_path / "cliches.json"
    bad.write_text("{not valid json")

    assert load_cliches(bad) == ""


def test_load_cliches_on_missing_file_returns_empty_not_raises(tmp_path):
    assert load_cliches(tmp_path / "does_not_exist.json") == ""


def test_load_cliches_renders_header_and_rules(tmp_path):
    path = tmp_path / "cliches.json"
    path.write_text(json.dumps({
        "header": "# Test map",
        "preamble": "pre",
        "sections": [{"title": "Section", "rules": [{"bad": ["x"], "say": ["y"]}]}],
    }))

    rendered = load_cliches(path)

    assert rendered.startswith("# Test map")
    assert "x" in rendered and "y" in rendered
