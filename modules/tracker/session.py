# =============================================================================
# Трекер module DB session — modules/tracker/session.py
# =============================================================================
# WHAT: Engine/sessionmaker FACTORIES for the tracker's own database + a lazy
#       default. The store (store.py) calls `async_session()` for queries and
#       `default_engine()` for create_tables.
#
# WHY factories, not an import-time engine (Step 3 of ARCHITECTURE_REVIEW.md):
#       an import-time engine bound every importer to the env-derived URL
#       before a test could substitute its own — see brain/db/session.py for
#       the full rationale; this file mirrors it.
#
# WHY `async_session()` is a function here (not a sessionmaker constant): the
#       tracker's store predates this refactor and opens sessions directly via
#       `async with async_session() as session:`. Keeping the same call shape
#       — a callable returning a fresh AsyncSession — means the whole store
#       keeps working while the engine still moves out of import time.
#
# HOW: `async with async_session() as session: ...` (the store commits
#      explicitly where it writes).
# =============================================================================

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from modules.tracker.config import settings


def make_engine(url: str) -> AsyncEngine:
    """One engine (connection pool) for the given database URL."""
    return create_async_engine(
        url,
        echo=False,
        # WHY pre-ping: every service holds a pool of live Postgres connections,
        # and a `docker compose up` that recreates the postgres container kills
        # all of them at once. Without this the pool keeps handing out corpses —
        # each one fails with "connection is closed" before being discarded — so
        # a routine restart of the stack shows up as the bot dying, one failed
        # request per pooled connection. Pre-ping spends one cheap round trip on
        # checkout and reconnects instead of erroring.
        pool_pre_ping=True,
        # And recycle before anything in the middle (docker's NAT, a firewall)
        # silently drops an idle connection we would otherwise trust.
        pool_recycle=1800,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory over an engine. expire_on_commit=False so ORM objects
    stay readable after commit (the API serialises freshly-created rows into
    responses)."""
    return async_sessionmaker(engine, expire_on_commit=False)


_default_engine: AsyncEngine | None = None
_default_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def default_engine() -> AsyncEngine:
    """The engine for the tracker's own database (lazy singleton)."""
    global _default_engine
    if _default_engine is None:
        _default_engine = make_engine(settings.database_url)
    return _default_engine


def default_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """The session factory over default_engine() (lazy singleton)."""
    global _default_sessionmaker
    if _default_sessionmaker is None:
        _default_sessionmaker = make_sessionmaker(default_engine())
    return _default_sessionmaker


def async_session() -> AsyncSession:
    """A fresh session from the lazy default — drop-in for the old import-time
    `async_session` sessionmaker the store already calls."""
    return default_sessionmaker()()
