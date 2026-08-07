# =============================================================================
# Кая local history — agents/kaya/history.py
# =============================================================================
# WHAT: The DB-backed implementation of Agent Core's History seam, storing Кая's
#       dialogue in her own `messages` table. This is what makes her memory of
#       the conversation survive a restart (InMemoryHistory would not).
#
# WHY it lives with the agent, not in agents.core: the lib defines the History
#       Protocol; each agent provides storage against its OWN DB (the plan's
#       per-agent local DB). Кая persists to Postgres; a test uses InMemoryHistory.
#
# WHY load() returns a trimmed window: only the newest N turns enter the prompt,
#       so the context never grows however long the chat runs. Older turns roll
#       off the window (anything durable lives in Brain's memory). The leading-
#       'user' trim (reused from agents.core) keeps the history API-valid.
#
# HOW: `DbHistory(window=30)` — passed to agents.core.Agent(history=...).
# =============================================================================

import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

# The wire-shape TypedDict is aliased: `Message` here is Кая's ORM row (the
# table), ChatMessage is what the prompt window returns.
from agents.core.history import Message as ChatMessage
from agents.core.history import trim_to_user_start
from agents.kaya.db.models import Message
from agents.kaya.db.session import get_session

# Local retention: the searchable archive lives in Brain (episodes, 1 year);
# this table only feeds the prompt window, so a year here too is generous.
RETENTION_DAYS = 365


class DbHistory:
    """Кая's conversation history in her own Postgres DB (a History)."""

    def __init__(
        self, window: int = 30, session_factory: async_sessionmaker | None = None
    ) -> None:
        self._window = window
        # None = Кая's default DB; tests pass a scratch-DB sessionmaker.
        self._sessions = session_factory

    async def load(self) -> list[ChatMessage]:
        """The newest `window` turns, chronological, trimmed to start at a
        'user' message (or empty)."""
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(Message).order_by(Message.id.desc()).limit(self._window)
            )
            rows = list(result.scalars().all())
        rows.reverse()  # newest-first query -> chronological
        messages = [{"role": m.role, "content": m.content} for m in rows]
        return trim_to_user_start(messages)

    async def append(self, role: str, content: str) -> None:
        """Persist one turn."""
        async with get_session(self._sessions) as session:
            session.add(Message(role=role, content=content))

    async def purge_old(self, days: int = RETENTION_DAYS) -> int:
        """Delete turns older than the retention window; returns how many.
        Called once at boot — this table needs no live sweeper, it only ever
        feeds the newest-N prompt window."""
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with get_session(self._sessions) as session:
            result = await session.execute(delete(Message).where(Message.created_at < cutoff))
            return result.rowcount or 0

    async def clear(self) -> int:
        """Wipe the whole history; return how many turns were removed."""
        async with get_session(self._sessions) as session:
            count = (
                await session.execute(select(func.count(Message.id)))
            ).scalar() or 0
            await session.execute(delete(Message))
        return count
