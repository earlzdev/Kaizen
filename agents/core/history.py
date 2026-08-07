# =============================================================================
# Local conversation history — agents/core/history.py
# =============================================================================
# WHAT: The seam for an agent's OWN dialogue history (its "local memory"). The
#       lib defines the History Protocol and ships an InMemoryHistory; a concrete
#       DB-backed history lives WITH each agent (its own DB, per the plan).
#
# WHY a seam, not a baked-in table: the plan gives each agent its own local DB
#       ("история диалога, своя БД агента"), separate from Brain's SHARED memory
#       about the owner. Agent Core must not dictate that storage — Кая persists
#       to Postgres, a test uses InMemoryHistory, a future agent could use
#       something else. The lib only needs load/append/clear.
#
# WHY the window + leading-'user' trim (ported from the monolith): only a fixed
#       window of newest messages is ever loaded, so the prompt never grows with
#       the conversation. The Anthropic API rejects a history that doesn't start
#       with a 'user' message, so we drop any leading assistant turns.
#
# HOW: pass any History implementation to Agent(). Roles are "user"/"assistant";
#       content is the plain text of each turn (tool traffic is NOT persisted —
#       it lives only inside a single loop run).
# =============================================================================

from collections.abc import Awaitable
from typing import Any, Protocol, TypedDict


class Message(TypedDict):
    """One turn on the Anthropic wire: role 'user'|'assistant'; content is
    plain text for stored turns, or provider content blocks (tool_use /
    tool_result lists) inside a live tool loop — hence Any, not str. Typed
    here (Step 7 of ARCHITECTURE_REVIEW.md) so the shape every loop/runner/
    history signature passes around is written down once."""

    role: str
    content: Any


class History(Protocol):
    """An agent's local conversation store."""

    def load(self) -> Awaitable[list[Message]]:
        """The recent window as Messages, oldest first, guaranteed to start
        with a 'user' message (or be empty)."""
        ...

    def append(self, role: str, content: str) -> Awaitable[None]:
        """Persist one turn."""
        ...

    def clear(self) -> Awaitable[int]:
        """Wipe the window; return how many turns were removed."""
        ...


def trim_to_user_start(messages: list[Message]) -> list[Message]:
    """Drop leading non-'user' turns so the history is API-valid. Shared by
    every History implementation (the monolith learned this the hard way)."""
    out = list(messages)
    while out and out[0].get("role") != "user":
        out.pop(0)
    return out


class InMemoryHistory:
    """A History kept in a list. For tests and stateless/ephemeral agents.

    `window` bounds how many newest turns load() returns, mirroring the DB-backed
    history's fixed context window."""

    def __init__(self, window: int = 30) -> None:
        self._messages: list[Message] = []
        self._window = window

    async def load(self) -> list[Message]:
        recent = self._messages[-self._window :]
        return trim_to_user_start(recent)

    async def append(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})

    async def clear(self) -> int:
        n = len(self._messages)
        self._messages.clear()
        return n
