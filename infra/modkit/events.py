# =============================================================================
# Delivery events — infra/modkit/events.py
# =============================================================================
# WHAT: DeliveryEvent — the typed contract for a Brain → agent push (today:
#       a fired reminder). Brain's sweeper serializes it; the agent's delivery
#       receiver validates it.
#
# WHY a shared model instead of a dict literal (Step 7 of
#       ARCHITECTURE_REVIEW.md): the contract used to exist only as
#       `{"kind": "reminder", "text": ...}` written in the sweeper and re-read
#       by hand in Кая's receiver — nothing pinned the two sides together. It
#       lives in infra/ because that is the one shared surface (like the gRPC
#       proto): Brain and every agent import the same class, so a field change
#       is a one-place change both sides see.
#
# WHY the event carries RAW text (no "⏰ Напоминание:" prefix): presentation
#       belongs to the agent that talks to the owner, not to Brain's scheduler.
#       The receiver decides how a reminder reads in its medium (Telegram text
#       for Кая; a future voice agent would SAY it differently).
#
# HOW: Brain: `DeliveryEvent(kind="reminder", text=...).model_dump()` -> POST.
#      Agent: `DeliveryEvent.model_validate(body)` -> 400 on shape mismatch.
# =============================================================================

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DeliveryEvent(BaseModel):
    """One Brain → agent push. `kind` selects how the receiver handles it;
    new kinds extend the Literal — both sides ship from this repo, so the
    contract moves in lockstep.

    kinds:
      "reminder"   — RELAY: the text is for the owner; the agent frames it in
                     its own medium and sends it.
      "agent_wake" — THINK: the agent left this note for ITSELF. Firing starts
                     a full agent turn seeded with the note, and whatever the
                     agent decides to say is what the owner sees. The note text
                     itself is internal and must never be relayed verbatim.
      "tracker"    — THINK, with a RELAY fallback: news from the project tracker
                     (a directive finished, an agent needs a decision, a project
                     wants to enroll). The agent retells it in its own voice and
                     may act on it — answering a question is a tool call, so it
                     needs a real turn. But unlike a self-note this text is
                     genuinely news the owner is waiting for, so a receiver that
                     cannot run a turn right now must relay it rather than drop
                     it. Losing "your PR is ready" is not an option."""

    model_config = ConfigDict(str_strip_whitespace=True)

    kind: Literal["reminder", "agent_wake", "tracker"]
    text: str
