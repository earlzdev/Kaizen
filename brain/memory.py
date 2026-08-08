# =============================================================================
# Brain shared memory store — brain/memory.py
# =============================================================================
# WHAT: The "shared memory about me" from the plan: durable facts (with vector
#       recall), the owner profile, and reminders — all in Brain's own DB, all
#       provenance-stamped. Ported from the monolith's memory/profile/reminder
#       services (principle 3: port over, don't rewrite), collapsed to
#       single-tenant (no user_id — the subject is always the one owner).
#
# WHY facts + vector recall (not longer history): store small distilled facts
#       with embeddings, and at reply time inject only the top-K relevant to the
#       current message. Memory grows without the prompt growing. Same cosine
#       search as the RAG pipeline, over the `facts` table.
#
# WHY dedup on remember (and why the threshold is TIGHT — Step 9): before
#       inserting we find the closest existing fact; only a true rephrasing
#       (cosine distance < the configured duplicate threshold, default 0.05)
#       refreshes that row. The old looser cut (0.15) DESTROYED information —
#       similar-but-distinct facts silently overwrote each other. When unsure,
#       we now keep both; recall's own distance cut keeps noise out of prompts.
#
# WHY every mutation calls record_change: multiple agents share this one memory,
#       so every write is attributed (change_log) and stamped with the acting
#       agent (agent_id) — the provenance the plan requires.
#
# HOW: constructed with an Embedder; `await store.remember("fact")`,
#       `await store.recall("query")`, profile get/set, reminder add/list.
# =============================================================================

import datetime
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from brain.config import settings
from brain.db.models import AUDIENCE_AGENT, AUDIENCE_OWNER, ChangeLog, Fact, Profile, Reminder
from brain.db.session import get_session
from brain.embedder import Embedder
from brain.provenance import actor_id, record_change

logger = logging.getLogger(__name__)

# Default number of facts to return from recall — small on purpose.
RECALL_TOP_K = 5
# Facts farther than this from the query are irrelevant; don't return them.
RECALL_MAX_DISTANCE = 0.75


class MemoryStore:
    """Facts, profile and reminders for the single owner, in Brain's DB."""

    def __init__(
        self,
        embedder: Embedder,
        session_factory: async_sessionmaker | None = None,
        duplicate_threshold: float | None = None,
    ) -> None:
        self._embedder = embedder
        # None = the service's default DB; tests pass a scratch-DB sessionmaker.
        self._sessions = session_factory
        # None = the configured default; tests pin an explicit value.
        self._dup_threshold = (
            duplicate_threshold
            if duplicate_threshold is not None
            else settings.memory_duplicate_threshold
        )

    # ----- facts -----------------------------------------------------------
    async def remember(self, fact: str) -> str:
        """Store a fact, or refresh a near-duplicate. Returns a status string."""
        vector = await self._embedder.embed(fact)
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(Fact, Fact.embedding.cosine_distance(vector).label("distance"))
                .where(Fact.embedding.isnot(None))
                .order_by(Fact.embedding.cosine_distance(vector))
                .limit(1)
            )
            row = result.first()

            if row is not None and row.distance < self._dup_threshold:
                existing = row.Fact
                old = existing.content
                existing.content = fact
                existing.embedding = vector
                existing.agent_id = actor_id()  # last writer wins
                await record_change(session, "fact", existing.id, "update", old=old, new=fact)
                logger.info("Fact refreshed: %r -> %r", old, fact)
                return f"Updated existing memory ('{old}') to: '{fact}'"

            new = Fact(content=fact, embedding=vector, agent_id=actor_id())
            session.add(new)
            await session.flush()  # assign new.id for the audit row
            await record_change(session, "fact", new.id, "create", new=fact)
            logger.info("Fact saved: %r", fact)
            return f"Remembered: '{fact}'"

    async def recall(self, query: str, top_k: int = RECALL_TOP_K) -> list[str]:
        """Facts most relevant to the query (semantic search)."""
        vector = await self._embedder.embed(query)
        # The distance cut is IN SQL, before LIMIT: filtering after the limit
        # (the old way) let top_k near-duplicates crowd a relevant fact out of
        # the window entirely — the query returned 5 far-off rows, dropped them
        # all in Python, and the agent recalled nothing.
        distance = Fact.embedding.cosine_distance(vector)
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(Fact.content)
                .where(Fact.embedding.isnot(None), distance < RECALL_MAX_DISTANCE)
                .order_by(distance)
                .limit(top_k)
            )
            return list(result.scalars().all())

    async def list_facts(self) -> list[Fact]:
        """All facts, newest first (for review / forgetting)."""
        async with get_session(self._sessions) as session:
            result = await session.execute(select(Fact).order_by(Fact.created_at.desc()))
            return list(result.scalars().all())

    async def list_change_log(self, limit: int = 100) -> list[ChangeLog]:
        """The provenance audit trail, newest first (for the admin panel):
        which agent changed what, when."""
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(ChangeLog).order_by(ChangeLog.at.desc()).limit(limit)
            )
            return list(result.scalars().all())

    async def forget(self, fact_id: int) -> bool:
        """Delete one fact by id. Returns False if it doesn't exist."""
        async with get_session(self._sessions) as session:
            fact = await session.get(Fact, fact_id)
            if fact is None:
                return False
            await record_change(session, "fact", fact.id, "delete", old=fact.content)
            await session.delete(fact)
            return True

    # ----- profile ---------------------------------------------------------
    async def get_profile(self) -> Profile | None:
        """The single owner profile row, or None if never set."""
        async with get_session(self._sessions) as session:
            result = await session.execute(select(Profile).limit(1))
            return result.scalar_one_or_none()

    async def set_profile(
        self, *, timezone: str | None = None, home_location: str | None = None
    ) -> str:
        """Upsert the single owner profile. Only provided fields change."""
        async with get_session(self._sessions) as session:
            result = await session.execute(select(Profile).limit(1))
            profile = result.scalar_one_or_none()
            if profile is None:
                profile = Profile()
                session.add(profile)
                await session.flush()
                action = "create"
            else:
                action = "update"
            if timezone is not None:
                profile.timezone = timezone
            if home_location is not None:
                profile.home_location = home_location
            profile.agent_id = actor_id()
            await record_change(
                session, "profile", profile.id, action,
                new=f"tz={profile.timezone}, home={profile.home_location}",
            )
            return f"Profile saved (timezone={profile.timezone}, home={profile.home_location})."

    # ----- reminders -------------------------------------------------------
    # A new reminder is treated as a duplicate of an existing pending one if the
    # text matches and the due time is within this window — guards against the
    # same request being processed twice (e.g. a redelivered message).
    _DUP_WINDOW = datetime.timedelta(minutes=2)

    async def add_reminder(
        self,
        text: str,
        due_at: datetime.datetime,
        recurrence: str = "none",
        tz: str | None = None,
        audience: str = AUDIENCE_OWNER,
    ) -> str:
        """Schedule a reminder at a CONCRETE instant. `due_at` MUST be timezone-
        aware (the caller localizes a naive time first); `tz` is the IANA zone it
        was set in, kept for display + DST-safe recurrence. A sweeper fires it.

        `audience` decides what firing MEANS: "owner" delivers the text to the
        owner; "agent" wakes the agent that set it, which then decides what to
        do. Dedup is scoped per audience — the agent planning to check in at
        18:00 must not collide with an owner reminder at 18:00.

        Dedups: if an identical-text pending reminder already exists within
        _DUP_WINDOW of `due_at`, the existing one is kept and no duplicate row is
        created (belt-and-braces against double-processing)."""
        if due_at.tzinfo is None:
            # Defensive: never store a naive time (the driver would assume UTC and
            # fire at the wrong wall-clock time). The tool layer localizes first.
            raise ValueError("due_at must be timezone-aware")
        if audience not in (AUDIENCE_OWNER, AUDIENCE_AGENT):
            raise ValueError(f"audience must be owner|agent, got '{audience}'")
        async with get_session(self._sessions) as session:
            existing = await session.execute(
                select(Reminder).where(
                    Reminder.is_done.is_(False),
                    Reminder.text == text,
                    Reminder.audience == audience,
                    Reminder.due_at >= due_at - self._DUP_WINDOW,
                    Reminder.due_at <= due_at + self._DUP_WINDOW,
                )
            )
            dup = existing.scalars().first()
            if dup is not None:
                return f"Reminder already set for {dup.due_at.isoformat()}: '{text}'"

            reminder = Reminder(
                text=text, due_at=due_at, tz=tz, recurrence=recurrence,
                audience=audience, agent_id=actor_id(),
            )
            session.add(reminder)
            await session.flush()
            await record_change(session, "reminder", reminder.id, "create", new=text)
            return f"Reminder set for {due_at.isoformat()}: '{text}'"

    async def delete_reminder(self, reminder_id: int) -> bool:
        """Cancel a reminder by id. Returns False if it doesn't exist."""
        async with get_session(self._sessions) as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None:
                return False
            await record_change(session, "reminder", reminder.id, "delete", old=reminder.text)
            await session.delete(reminder)
            return True

    async def list_reminders(self, *, include_done: bool = False) -> list[Reminder]:
        """Pending reminders (or all), soonest first."""
        async with get_session(self._sessions) as session:
            query = select(Reminder).order_by(Reminder.due_at.asc())
            if not include_done:
                query = query.where(Reminder.is_done.is_(False))
            result = await session.execute(query)
            return list(result.scalars().all())

    async def due_reminders(self, now: datetime.datetime) -> list[Reminder]:
        """Reminders that have come due and not yet delivered (Phase 6 sweep)."""
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(Reminder)
                .where(Reminder.is_done.is_(False), Reminder.due_at <= now)
                .order_by(Reminder.due_at.asc())
            )
            return list(result.scalars().all())

    async def mark_reminder_delivered(self, reminder_id: int) -> None:
        """Called after a reminder is successfully pushed. A one-off is marked
        done; a recurring one advances to its next occurrence so it fires again.

        WHY advance instead of delete: the row is the durable schedule. Moving
        due_at forward keeps a daily/weekly reminder alive across restarts, same
        as the monolith's reminder table."""
        async with get_session(self._sessions) as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None:
                return
            step = None
            if reminder.recurrence == "daily":
                step = datetime.timedelta(days=1)
            elif reminder.recurrence == "weekly":
                step = datetime.timedelta(weeks=1)
            if step is None:
                reminder.is_done = True
                return
            # Advance to the next occurrence STRICTLY in the future, preserving
            # LOCAL wall-clock time (so "daily at 09:00" stays 09:00 across a DST
            # change, not drifting by an hour). We do the arithmetic on the naive
            # local time and re-localize, which recomputes the offset. If Brain
            # was down for several periods, catch up in one jump so the owner gets
            # ONE reminder now, not a stale flurry across the next N sweeps.
            now = datetime.datetime.now(datetime.timezone.utc)
            try:
                zone = ZoneInfo(reminder.tz) if reminder.tz else datetime.timezone.utc
            except Exception:
                zone = datetime.timezone.utc
            local = reminder.due_at.astimezone(zone).replace(tzinfo=None) + step
            while local.replace(tzinfo=zone).astimezone(datetime.timezone.utc) <= now:
                local = local + step
            reminder.due_at = local.replace(tzinfo=zone)
