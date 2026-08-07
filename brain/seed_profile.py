# =============================================================================
# Initial profile/fact seeding — brain/seed_profile.py
# =============================================================================
# WHAT: A one-shot CLI that bulk-loads facts and profile fields into Brain's
#       shared memory from a plain-text file, instead of typing them to Кая
#       one message at a time. Useful right after a fresh install, when
#       there's a paragraph of context about yourself you'd rather paste once
#       than narrate turn by turn.
#
# WHY a script and not a chat message: Кая's normal path (memory_write) goes
#       through the model — you tell her something, she distills it into a
#       fact and calls the tool. That's the right shape for an ongoing
#       conversation, but a wall of background info pasted into Telegram
#       either gets summarized lossily or costs a slow, expensive turn just to
#       ingest text. This writes facts directly, in whatever form you gave
#       them — no model in the loop, no distillation. Write them the way
#       soul.md asks Кая to (one fact per line, third person, self-contained)
#       if you want them to read the same way hers do.
#
# WHY it works with no acting agent: brain.provenance.record_change degrades
#       to a no-op when no agent is set on the ContextVar (see its own WHY) —
#       exactly the "system/seed write" case this is. Seeded facts land with
#       agent_id=None instead of attributed to a specific agent.
#
# WHY it doesn't go through Кая's tool-call path at all: it talks to
#       MemoryStore directly, the same object brain/main.py hands to the MCP
#       server — same dedup, same embeddings, same table. A seeded fact is
#       indistinguishable later from one Кая saved herself.
#
# INPUT FORMAT: a plain-text file with two optional sections, either can be
#       skipped:
#
#         # Profile
#         timezone: Europe/Lisbon
#         home_location: Lisbon, Portugal
#
#         # Facts
#         Works as a backend engineer, focused on distributed systems.
#         Prefers dark roast coffee, no sugar.
#         - Learning Japanese, practices 20 minutes daily.
#
#       Section headers are matched case-insensitively on "profile"/"facts"
#       appearing after a leading '#'. One entry per non-empty line in
#       either section; a leading "- " bullet is stripped if present (both
#       sections accept it, so `- timezone: ...` and `- some fact.` both
#       work). Blank lines and any other '#' heading are ignored.
#
# HOW: docker compose --env-file .env -f deploy/docker-compose.yml exec brain \
#        python -m brain.seed_profile /path/inside/container/profile.txt
#      (mount the file into the container, or `docker compose cp` it in
#      first — there's no host bind-mount for this by default.)
# =============================================================================

import asyncio
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from brain.db.session import default_engine
from brain.embedder import Embedder
from brain.memory import MemoryStore


def _parse(text: str) -> tuple[dict[str, str], list[str]]:
    """The input file -> (profile fields, fact lines)."""
    profile: dict[str, str] = {}
    facts: list[str] = []
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip().lower()
            if "profile" in heading:
                section = "profile"
            elif "facts" in heading:
                section = "facts"
            else:
                section = None
            continue
        # "- " (a real bullet) is stripped in both sections; "-hyphenated-word"
        # is not — the two-char prefix (hyphen AND a space) is what
        # distinguishes them. A bullet with nothing after it ("-" alone, or
        # "- " that strip() already reduced to "-") has no content worth
        # keeping, in either section.
        entry = line[2:].strip() if line.startswith("- ") else line
        if not entry or entry == "-":
            if section == "profile":
                # Same "unusable line" case as an unrecognized key below —
                # worth telling the owner either way, not just silently
                # dropping it because it happened to be an empty bullet.
                print(f"seed_profile: ignoring unrecognized Profile line: {line!r}", file=sys.stderr)
            continue
        if section == "profile":
            key, sep, value = entry.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if sep and key in ("timezone", "home_location") and value:
                profile[key] = value
            else:
                print(f"seed_profile: ignoring unrecognized Profile line: {line!r}", file=sys.stderr)
        elif section == "facts":
            facts.append(entry)
    return profile, facts


async def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m brain.seed_profile <path-to-file>", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        sys.exit(1)

    profile, facts = _parse(path.read_text(encoding="utf-8"))
    if not profile and not facts:
        print("nothing to seed — no '# Profile' or '# Facts' section found", file=sys.stderr)
        sys.exit(1)

    # Fail fast, before paying the embedder-load wait: a typo'd IANA name
    # (e.g. "Europe/Moskow") would otherwise sit in the DB silently until the
    # first reminder that needs it blew up somewhere else entirely.
    if "timezone" in profile:
        try:
            ZoneInfo(profile["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            print(f"seed_profile: not a valid IANA timezone: {profile['timezone']!r}", file=sys.stderr)
            sys.exit(1)

    embedder = Embedder()
    await embedder.warmup()
    store = MemoryStore(embedder)

    try:
        if profile:
            result = await store.set_profile(
                timezone=profile.get("timezone"), home_location=profile.get("home_location")
            )
            print(result)

        for fact in facts:
            result = await store.remember(fact)
            print(result)
    except Exception as e:
        print(f"seed_profile: database error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await default_engine().dispose()

    print(f"Done: {'profile set, ' if profile else ''}{len(facts)} fact(s) processed.")


if __name__ == "__main__":
    asyncio.run(main())
