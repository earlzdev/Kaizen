# =============================================================================
# Subscription-quota detection — infra/quota.py
# =============================================================================
# WHAT: Recognises "the Claude subscription is spent" in whatever the CLI wrote,
#       and digs the reset time out of it.
#
# WHY it is shared rather than written twice: a spent quota exits non-zero
#       exactly like a crash, so without this the report says "exited with code
#       1" and the owner goes looking for a bug that is actually a clock. Both
#       surfaces that run the CLI — Кая (agents/core/cli.py) and every project's
#       Warden (infra/wardenkit/clirunner.py) — hit the same wall on the same
#       subscription, and two copies of these patterns would drift the moment
#       the CLI changed its wording.
#
# WHY it lives directly in `infra/` and is VENDORED into projects: it is the one
#       thing wardenkit imports beyond the generated stubs, so
#       `infra/agentkit/render.py` copies this file alongside it. Keep it
#       dependency-free — stdlib only — for exactly that reason.
#
# HOW:  spent, reset_at = detect_quota(f"{stderr}\n{result_text}")
# =============================================================================

from __future__ import annotations

import re
import time

# What a spent subscription says. Matched case-insensitively against the whole
# captured output, because the CLI puts this in different places depending on
# how far it got before it gave up. Every entry is an OBSERVED string, with the
# source noted — do not "tidy" this list from memory, the CLI's wording shifts
# between versions and a marker nobody has seen is dead weight:
#   "You've hit your session limit · resets 8:20am (UTC)"   (kaya logs, 2026-08)
#   "Claude AI usage limit reached|1719964800"              (stream result event)
#   "5-hour limit reached ∙ resets 3am" / weekly variant    (interactive CLI)
_MARKERS = (
    "usage limit reached",
    "reached your usage limit",
    "hit your session limit",
    "hit your usage limit",
    "session limit reached",
    "5-hour limit reached",
    "weekly limit reached",
)

# The machine-readable form: "Claude AI usage limit reached|1719964800".
_EPOCH_RE = re.compile(r"limit reached\|(\d{9,})", re.I)

# The prose form, in pieces so each part is checkable on its own:
#   - "at" is optional and may sit before OR after the day
#     ("resets at 7pm" / "resets 8:20am" / "resets tomorrow at 9am")
#   - the day is a real day word, not any word — otherwise "resets in 3 hours"
#     captures "in 3" and the owner is told the reset is at "in 3"
#   - the time REQUIRES ":MM" or am/pm, so a bare digit in prose never passes
#   - a trailing "(UTC)"-style zone is CAPTURED, not dropped: these containers
#     run in UTC, and "Сброс в 8:20" read as local time by an owner in MSK is
#     worse than no time at all
_DAY = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?|today|tomorrow"
_TIME = r"[0-9]{1,2}(?::[0-9]{2}\s*(?:am|pm)?|\s*(?:am|pm))"
_CLOCK_RE = re.compile(
    rf"reset(?:s|ting)?\s+(?:at\s+)?(?:({_DAY})\s+)?(?:at\s+)?"
    rf"({_TIME})(\s*\([^)\n]{{1,16}}\))?",
    re.I,
)

# Sanity bounds for the epoch form: outside 2020..2100 it is not a reset time.
# 13 digits is the same timestamp in milliseconds — seen from JS-side tooling.
_EPOCH_MIN = 1577836800      # 2020-01-01
_EPOCH_MAX = 4102444800      # 2100-01-01


def detect_quota(text: str) -> tuple[bool, str]:
    """(is the subscription spent, when it resets as a human-readable string).

    The second value is "" when the message did not say — a real case that
    must be reported as "no stated time", never as a made-up one.

    Matching is case-insensitive; the RETURNED string keeps the original
    casing ("8:20am (UTC)", not "8:20am (utc)") because it is shown to the
    owner verbatim.
    """
    if not text:
        return False, ""
    if not any(m in text.lower() for m in _MARKERS):
        return False, ""
    return True, _reset_hint(text)


def _reset_hint(text: str) -> str:
    epoch = _EPOCH_RE.search(text)
    if epoch:
        stamp = int(epoch.group(1))
        if stamp > _EPOCH_MAX and _EPOCH_MIN <= stamp // 1000 <= _EPOCH_MAX:
            stamp //= 1000   # milliseconds
        if _EPOCH_MIN <= stamp <= _EPOCH_MAX:
            try:
                local = time.localtime(stamp)
                # %Z, always: the process very likely lives in UTC (compose
                # sets no TZ), and an unlabelled "10:46" is silently 3 hours
                # off for an owner in MSK. With TZ set, this improves by
                # itself and keeps saying which zone it means.
                fmt = "%H:%M %Z" if time.localtime()[:3] == local[:3] else "%a %H:%M %Z"
                return time.strftime(fmt, local).strip()
            except (ValueError, OverflowError, OSError):
                pass
        # A matched-but-absurd epoch: fall through to the prose form rather
        # than printing a year-2262 clock reading.
    clock = _CLOCK_RE.search(text)
    if not clock:
        return ""
    day, when, zone = clock.group(1), clock.group(2), clock.group(3)
    parts = [p.strip() for p in (day, when, zone) if p]
    return " ".join(" ".join(parts).split())


__all__ = ["detect_quota"]
