# =============================================================================
# Brain conversation archive — brain/episodes.py
# =============================================================================
# WHAT: The episodic memory: every owner↔agent exchange, embedded for semantic
#       search («а что мы решали в марте про отпуск?»). Complements the FACT
#       memory (brain/memory.py): facts are distilled one-liners the agent chose
#       to save; episodes are the raw dialogue, archived automatically.
#
# WHY search is scoped to the calling agent by default (owner's decision): the
#       archive is shared storage, but Кая searching Кузя's dialogs uninvited
#       would be surprising. The caller's identity comes from the provenance
#       ContextVar (set by the server from the bearer token) — an agent cannot
#       spoof "mine", and widening to another agent's dialogs must be an
#       explicit argument (the tool description tells the model to pass it only
#       when the owner explicitly asks).
#
# WHY no change_log rows (unlike facts): episodes are high-volume append-only
#       traffic — one row per message. Auditing each write would double the
#       table for zero insight; the row itself already records who and when.
#
# WHY retention here (not in each agent): one place, one rule. A daily loop
#       deletes episodes older than RETENTION_DAYS — the owner keeps a year of
#       searchable history without the table growing forever.
#
# HOW: `EpisodeStore(embedder)`; log()/search() are the tool handlers,
#      run_retention_forever() is started as a background task in main.py.
# =============================================================================

import asyncio
import datetime
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from brain.db.models import Episode
from brain.db.session import get_session
from brain.embedder import Embedder
from brain.provenance import actor_id, actor_slug

logger = logging.getLogger(__name__)

# Owner's decision (2026-07-29): a year of history, older is deleted.
RETENTION_DAYS = 365
# How often the retention loop wakes. Daily granularity is plenty for a
# year-long window.
RETENTION_SWEEP_SECONDS = 24 * 3600

# Episodes farther than this (cosine distance) from the query are noise.
SEARCH_MAX_DISTANCE = 0.75
SEARCH_TOP_K = 5

# Cap stored/embedded text: a pathological turn (huge research answer) must not
# bloat rows; the head of a message carries its topic well enough for search.
_MAX_STORED_CHARS = 4000
# What each side of an exchange is trimmed to in search RESULTS (the model just
# needs enough to recall the conversation, not the full text).
_EXCERPT_CHARS = 400


class EpisodeStore:
    """The owner's dialogue archive across all agents, with semantic search."""

    def __init__(
        self, embedder: Embedder, session_factory: async_sessionmaker | None = None
    ) -> None:
        self._embedder = embedder
        # None = the service's default DB; tests pass a scratch-DB sessionmaker.
        self._sessions = session_factory

    async def log(self, owner_text: str, agent_text: str) -> str:
        """Archive one exchange, attributed to the calling agent."""
        slug = actor_slug()
        owner_text = owner_text[:_MAX_STORED_CHARS]
        agent_text = agent_text[:_MAX_STORED_CHARS]
        # One vector per exchange, over both sides: a later query may match
        # either what the owner asked or what the agent answered.
        vector = await self._embedder.embed(f"{owner_text}\n{agent_text}")
        async with get_session(self._sessions) as session:
            session.add(
                Episode(
                    agent_id=actor_id(),
                    agent_slug=slug,
                    owner_text=owner_text,
                    agent_text=agent_text,
                    embedding=vector,
                )
            )
        return "Archived."

    async def search(self, query: str, scope: str = "mine") -> str:
        """Semantic search over the archive. scope: 'mine' (default — only the
        calling agent's own dialogs), 'all', or another agent's slug."""
        distance = Episode.embedding.cosine_distance(await self._embedder.embed(query))
        # Distance cut in SQL, before LIMIT (same fix as facts recall): filtering
        # after the limit let top_k noise rows push a real match out of the window.
        stmt = (
            select(Episode)
            .where(Episode.embedding.isnot(None), distance < SEARCH_MAX_DISTANCE)
            .order_by(distance)
            .limit(SEARCH_TOP_K)
        )
        if scope == "mine":
            stmt = stmt.where(Episode.agent_slug == actor_slug())
        elif scope != "all":
            stmt = stmt.where(Episode.agent_slug == scope)
        async with get_session(self._sessions) as session:
            rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return "No archived conversations match that query."
        lines = []
        for ep in rows:
            when = ep.created_at.strftime("%Y-%m-%d")
            lines.append(
                f"[{when}, {ep.agent_slug}]\n"
                f"  Owner: {ep.owner_text[:_EXCERPT_CHARS]}\n"
                f"  Agent: {ep.agent_text[:_EXCERPT_CHARS]}"
            )
        return "Archived conversations matching the query:\n" + "\n".join(lines)

    async def purge_older_than(self, days: int = RETENTION_DAYS) -> int:
        """Delete episodes past the retention window; returns how many."""
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with get_session(self._sessions) as session:
            result = await session.execute(delete(Episode).where(Episode.created_at < cutoff))
            return result.rowcount or 0

    async def run_retention_forever(self) -> None:
        """Daily retention sweep. One failed iteration never stops the loop."""
        logger.info("Episode retention started (%d days, daily sweep)", RETENTION_DAYS)
        while True:
            try:
                purged = await self.purge_older_than()
                if purged:
                    logger.info("Episode retention: deleted %d old episode(s)", purged)
            except Exception:
                logger.exception("Episode retention sweep failed; will retry")
            await asyncio.sleep(RETENTION_SWEEP_SECONDS)
