# =============================================================================
# Roster vocabulary — modules/tracker/roster.py
# =============================================================================
# WHAT: The ONE standard vocabulary for what an agent IS inside a project's
#       fleet — its `tier` (where it sits in the hierarchy) and its `area`
#       (which part of the project it works on) — plus `normalize()`, which
#       turns whatever a project sent into that vocabulary.
#
# WHY this exists at all (the refactor): the fleet chart first derived structure
#       in the PANEL, at render time, by matching words in an agent's slug. That
#       was wrong in three ways at once:
#         1. every project's fleet is different (different personas, different
#            pipelines), so a guess that fits one fleet misfits the next;
#         2. the guess was re-run on every render, in JavaScript, where nothing
#            could see or correct it — a persona filed under the wrong team
#            looked like a bug in the project, not in the panel;
#         3. nothing else could use it. Кая's `describe_project` saw a flat
#            list, because the structure existed only inside the browser.
#       Normalising HERE, once, at the moment a roster is stored, fixes all
#       three: the database holds the standard vocabulary, every reader agrees,
#       and a project that disagrees with the guess can simply declare the truth.
#
# WHY these five tiers and not a free-form title: a fleet chart has to draw
#       SOMETHING for every project, including ones whose personas are named
#       nothing like anyone else's. Five tiers are the smallest set that still
#       draws a real hierarchy, and every fleet this design targets maps onto
#       them. `role` stays free text and is what the owner actually reads —
#       "Backend Team Lead" is the label; `lead` is the shape.
#
# HOW: store.sync_roster() and store.create_agent() call `normalize()`. Nothing
#      else should ever guess — downstream code reads `tier` and `area` and
#      trusts them.
# =============================================================================

import re

# Where a member sits in the fleet, from the top down. ORDER IS MEANINGFUL:
# the chart draws them in this sequence, and `tier_rank` compares by it.
OWNER = "owner"            # the human this work is for. Not an agent.
PRODUCT = "product"        # the Product Owner agent: owns WHAT is built and why
ARCHITECT = "architect"    # decomposes a Directive into work for the leads
LEAD = "lead"              # owns one area; decomposes for its developers
DEVELOPER = "developer"    # does the work
REVIEWER = "reviewer"      # checks it — code review, security, QA, design

AGENT_TIERS = (OWNER, PRODUCT, ARCHITECT, LEAD, DEVELOPER, REVIEWER)

# Reviewers usually serve every area rather than living in one, so they get
# their own band at the bottom of the chart instead of a column.
CROSS_CUTTING = "cross-cutting"


def tier_rank(tier: str) -> int:
    """Position in the hierarchy; unknown tiers sort with the developers."""
    try:
        return AGENT_TIERS.index(tier)
    except ValueError:
        return AGENT_TIERS.index(DEVELOPER)


# --- the fallback, for a project that declares nothing ----------------------
# These only ever fill in a BLANK field. A project that says what it means is
# never second-guessed. Kept in Python (not the panel) so the guess is made
# once, is stored, and can be corrected.
#
# OWNER is deliberately NOT guessable. It is the human, seeded by the Hub
# (store.ensure_owner) — and a persona slugged `product-designer` or
# `product-analyst` would otherwise be promoted to the top of the chart AND
# stop the real owner from ever being seeded, since that check looks for any
# owner-tier row. A project may still DECLARE tier="owner" if it means it.
#
# PRODUCT is guessed, but only from the PAIR of words "product" + "owner"
# (see `normalize`) — never from "product" alone, for exactly the reason above:
# `product-designer` is a designer and `product-analyst` is an analyst. The PO
# is an AI agent that owns scope, so it is deliberately a tier of its own rather
# than a second `owner`: the human stays the root of the chart, `ensure_owner`
# keeps seeding it, and `kind` stays "ai" here instead of being forced to
# "human".
_TIER_WORDS = {
    "architect": ARCHITECT, "principal": ARCHITECT, "overseer": ARCHITECT,
    "alfred": ARCHITECT, "solution": ARCHITECT,
    "lead": LEAD, "teamlead": LEAD, "manager": LEAD,
    "dev": DEVELOPER, "developer": DEVELOPER, "engineer": DEVELOPER,
    "infra": DEVELOPER, "designer": DEVELOPER, "analyst": DEVELOPER,
    "researcher": DEVELOPER, "writer": DEVELOPER,
    "reviewer": REVIEWER, "review": REVIEWER, "qa": REVIEWER,
    "security": REVIEWER, "auditor": REVIEWER, "tester": REVIEWER,
}
_AREA_WORDS = {
    "backend": "backend", "api": "backend", "server": "backend",
    "frontend": "frontend", "ui": "frontend", "web": "frontend",
    "mobile": "mobile", "ios": "ios", "android": "android", "flutter": "mobile",
    "infra": "infra", "devops": "infra", "platform": "infra", "sre": "infra",
    "security": "security", "qa": "qa", "test": "qa",
    "design": "design", "figma": "design", "ux": "design",
    "data": "data", "ml": "data", "research": "research", "docs": "docs",
}


def _s(value) -> str:
    """Coerce any JSON scalar to a stripped string.

    `normalize` is called with a hand-written body on `POST /agents` as well as
    with protobuf (which is already typed), so `{"tier": 3}` must not become an
    AttributeError and a 500. Anything that isn't a string simply isn't one of
    our vocabulary words, and falls through to the guess.
    """
    return "" if value is None else str(value).strip()


def _words(*parts: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", " ".join(parts).lower()) if w]


def clean_area(value: str | None) -> str:
    """Normalise an area to a stable key: lowercase, single dashes, no spaces.

    It is both a grouping key and a column heading in the panel, so `Backend`
    and `backend ` arriving in one manifest must not become two different teams.

    WHY `\\w` and not `[a-z0-9]`: an ASCII-only filter turns "Бэкенд" into an
    empty string, and a project that names its areas in Russian would have EVERY
    team collapse into the one unnamed "other" column — the areas it declared
    silently lost. The panel is what needs an ASCII CSS class, and it makes its
    own (see `areaClass` there); the stored value only has to be a stable,
    readable key.
    """
    slug = re.sub(r"[^\w]+", "-", (value or "").lower(), flags=re.UNICODE).strip("-_")
    return slug[:64]


def normalize(spec: dict) -> dict:
    """Turn one roster entry into the standard vocabulary.

    Accepts whatever a project sent — `slug`/`name`, `role`, `model`, and the
    optional `tier` / `area` / `reports_to` — and returns a dict with every
    field filled and normalised. Declared values always win; blanks are guessed
    from the slug and role, and the guess is stored so it can be seen and fixed.
    """
    # `slug` identifies, `name` labels. A manifest usually sends both
    # ("architect-xavier" / "Charles Xavier"); when it sends only one, that one
    # does both jobs.
    slug = _s(spec.get("slug"))
    label = _s(spec.get("name"))
    name = slug or label
    display = label if (label and label != name) else ""
    role = _s(spec.get("role"))
    words = _words(name, role)

    tier = _s(spec.get("tier")).lower()
    declared_tier = tier in AGENT_TIERS
    if not declared_tier:
        # "product owner" as a PAIR, before the single-word table: a persona
        # called `product-owner-ohno` has no single word that identifies it
        # (`owner` is not guessable and `product` must not be), so without this
        # the agent that sits ABOVE the architect would be filed as a developer.
        if "product" in words and "owner" in words:
            tier = PRODUCT
        else:
            # The LOWEST tier any word implies wins, so "backend-lead-tesla" is
            # a lead rather than being dragged down to developer by "backend".
            found = [_TIER_WORDS[w] for w in words if w in _TIER_WORDS]
            tier = min(found, key=tier_rank) if found else DEVELOPER

    area = clean_area(spec.get("area"))
    declared_area = bool(area)
    if not area:
        for w in words:
            if w in _AREA_WORDS:
                area = _AREA_WORDS[w]
                break
    # A reviewer serves every area unless it SAYS otherwise. The guess must not
    # decide this: "security-holmes" and "ui-reviewer-rams" both contain an area
    # word, and letting that win would file the security reviewer under a
    # security team and the design reviewer under frontend — pulling both out of
    # the cross-cutting band where a reviewer belongs. Declaring an area still
    # works, for the project that really does embed a reviewer in one team.
    if tier == REVIEWER and not declared_area:
        area = CROSS_CUTTING
    # The people at the top span every area; giving them one would file them
    # under a single team in the chart.
    if tier in (OWNER, PRODUCT, ARCHITECT):
        area = ""

    return {
        "name": name,
        "display_name": display or None,
        "role": role or None,
        "model": _s(spec.get("model")) or None,
        "tier": tier,
        "area": area or None,
        "reports_to": _s(spec.get("reports_to")) or None,
        "kind": "human" if tier == OWNER else (_s(spec.get("kind")) or "ai"),
        # Which fields the project actually SAID, as opposed to which ones we
        # filled in. `sync_roster` needs this to tell a correction from a
        # guess: `tier` always has a value (it defaults to `developer`), so
        # without this flag every refresh would overwrite a tier the owner had
        # fixed by hand with the guess that was wrong in the first place.
        "declared": {
            "tier": declared_tier,
            "area": declared_area,
            "reports_to": bool(_s(spec.get("reports_to"))),
        },
    }


__all__ = [
    "AGENT_TIERS", "ARCHITECT", "CROSS_CUTTING", "DEVELOPER", "LEAD", "OWNER",
    "PRODUCT", "REVIEWER", "clean_area", "normalize", "tier_rank",
]
