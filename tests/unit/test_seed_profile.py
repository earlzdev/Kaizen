# =============================================================================
# Unit tests — brain/seed_profile.py's _parse()
# =============================================================================
# WHAT: The one part of the seed script that can silently do the wrong thing —
#       a hand-rolled section/line parser with no schema to catch mistakes.
# WHY: everything else in seed_profile.py is a thin pass-through to
#      MemoryStore, already exercised indirectly wherever that's tested; this
#      function is the actual logic worth covering.
# =============================================================================

from brain.seed_profile import _parse


def test_profile_then_facts():
    text = """# Profile
timezone: Europe/Moscow
home_location: Lisbon, Portugal

# Facts
Works as a backend engineer.
- Learning Japanese, practices daily.
"""
    profile, facts = _parse(text)
    assert profile == {"timezone": "Europe/Moscow", "home_location": "Lisbon, Portugal"}
    assert facts == ["Works as a backend engineer.", "Learning Japanese, practices daily."]


def test_facts_before_profile_still_parses_both():
    text = """# Facts
First fact.

# Profile
timezone: UTC
"""
    profile, facts = _parse(text)
    assert profile == {"timezone": "UTC"}
    assert facts == ["First fact."]


def test_facts_only_no_profile_section():
    profile, facts = _parse("# Facts\nOnly a fact.\n")
    assert profile == {}
    assert facts == ["Only a fact."]


def test_profile_only_no_facts_section():
    profile, facts = _parse("# Profile\ntimezone: UTC\n")
    assert profile == {"timezone": "UTC"}
    assert facts == []


def test_no_headings_at_all():
    assert _parse("just some text\nwith no section markers\n") == ({}, [])


def test_empty_file():
    assert _parse("") == ({}, [])


def test_unknown_heading_is_ignored():
    profile, facts = _parse("# Notes\nsome text\n")
    assert profile == {} and facts == []


def test_fact_containing_a_colon_is_kept_whole():
    profile, facts = _parse("# Facts\nGoal: ship v2 by Friday.\n")
    assert facts == ["Goal: ship v2 by Friday."]


def test_profile_value_containing_a_colon():
    profile, _ = _parse("# Profile\nhome_location: Lisbon, Portugal (GMT: +0)\n")
    assert profile["home_location"] == "Lisbon, Portugal (GMT: +0)"


def test_unknown_profile_key_is_dropped():
    profile, _ = _parse("# Profile\nfavorite_color: blue\n")
    assert profile == {}


def test_bullet_prefix_stripped():
    _, facts = _parse("# Facts\n- Bulleted fact.\n")
    assert facts == ["Bulleted fact."]


def test_hyphen_initial_word_not_mistaken_for_a_bullet():
    _, facts = _parse("# Facts\n-hyphenated-word is not a bullet.\n")
    assert facts == ["-hyphenated-word is not a bullet."]


def test_bare_bullet_with_no_content_is_dropped():
    _, facts = _parse("# Facts\n- \nreal fact\n")
    assert facts == ["real fact"]


def test_unrecognized_profile_line_is_skipped_not_crashed(capsys):
    profile, _ = _parse("# Profile\nnot a valid line\ntimezone: UTC\n")
    assert profile == {"timezone": "UTC"}
    assert "ignoring" in capsys.readouterr().err


def test_bullet_prefix_stripped_in_profile_section_too():
    profile, _ = _parse("# Profile\n- timezone: Europe/Moscow\n")
    assert profile == {"timezone": "Europe/Moscow"}


def test_empty_profile_value_is_rejected_not_stored(capsys):
    profile, _ = _parse("# Profile\ntimezone:\ntimezone: UTC\n")
    assert profile == {"timezone": "UTC"}
    assert "ignoring" in capsys.readouterr().err
