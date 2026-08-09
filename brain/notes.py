# =============================================================================
# Brain notes store — brain/notes.py
# =============================================================================
# WHAT: Explicit, owner-requested notes — content plus an agent-assigned
#       category/tags and a vector embedding for semantic search. Mirrors
#       brain/memory.py's MemoryStore shape (embed on write, cosine search on
#       read, provenance-stamped), but notes are never auto-saved: every row
#       here exists because the owner explicitly asked for it (soul.md's
#       Notes section gates that on the agent side, not here).
#
# WHY category/tag filtering happens in SQL, not Python: `list_notes` filters
#       by exact category and/or tag membership at the query level so a large
#       note list never gets pulled into memory just to be discarded.
#
# HOW: constructed with an Embedder; `await store.save_note(...)`,
#       `list_notes(...)`, `search_notes(...)`, `list_categories()`,
#       `list_tags()`, `forget_note(id)`.
# =============================================================================

from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import async_sessionmaker

from brain.db.models import Note
from brain.db.session import get_session
from brain.embedder import Embedder
from brain.provenance import actor_id, record_change

# Notes farther than this from the query are irrelevant; don't return them.
SEARCH_MAX_DISTANCE = 0.75


class NoteStore:
    """Explicit owner notes, categorized and tagged, in Brain's DB."""

    def __init__(
        self,
        embedder: Embedder,
        session_factory: async_sessionmaker | None = None,
    ) -> None:
        self._embedder = embedder
        # None = the service's default DB; tests pass a scratch-DB sessionmaker.
        self._sessions = session_factory

    async def save_note(
        self,
        content: str,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Store one note. Returns a status string."""
        vector = await self._embedder.embed(content)
        async with get_session(self._sessions) as session:
            note = Note(
                content=content,
                category=category,
                tags=tags,
                embedding=vector,
                agent_id=actor_id(),
            )
            session.add(note)
            await session.flush()  # assign note.id for the audit row
            await record_change(session, "note", note.id, "create", new=content)
            return f"Note saved [{note.id}] (category={category}, tags={tags})."

    async def list_notes(
        self, category: str | None = None, tag: str | None = None
    ) -> list[Note]:
        """Notes newest first, optionally filtered by exact category and/or a
        tag it must contain."""
        async with get_session(self._sessions) as session:
            query = select(Note).order_by(Note.created_at.desc())
            if category is not None:
                query = query.where(Note.category == category)
            if tag is not None:
                query = query.where(Note.tags.contains([tag]))
            result = await session.execute(query)
            return list(result.scalars().all())

    async def search_notes(self, query: str, top_k: int = 5) -> list[Note]:
        """Notes most relevant to the query (semantic search)."""
        vector = await self._embedder.embed(query)
        distance = Note.embedding.cosine_distance(vector)
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(Note)
                .where(Note.embedding.isnot(None), distance < SEARCH_MAX_DISTANCE)
                .order_by(distance)
                .limit(top_k)
            )
            return list(result.scalars().all())

    async def list_categories(self) -> list[str]:
        """Every distinct category in use, alphabetical."""
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(Note.category)
                .where(Note.category.isnot(None))
                .distinct()
                .order_by(Note.category)
            )
            return list(result.scalars().all())

    async def list_tags(self) -> list[str]:
        """Every distinct tag in use, alphabetical (tags are stored as an
        array per note, so this unnests before deduping)."""
        tag = func.unnest(Note.tags).label("tag")
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(tag).where(Note.tags.isnot(None)).distinct().order_by(tag)
            )
            return list(result.scalars().all())

    async def forget_note(self, note_id: int) -> bool:
        """Delete one note by id. Returns False if it doesn't exist."""
        async with get_session(self._sessions) as session:
            note = await session.get(Note, note_id)
            if note is None:
                return False
            await record_change(session, "note", note.id, "delete", old=note.content)
            await session.delete(note)
            return True
