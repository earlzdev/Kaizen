# =============================================================================
# Кая DB session factory — agents/kaya/db/session.py
# =============================================================================
# WHAT: Engine/sessionmaker FACTORIES for Кая's own database + a lazy default
#       bound to settings.database_url. Same shape as Brain's session module,
#       so she never shares a pool or a database with Brain or anyone else.
#
# WHY factories, not an import-time engine (Step 3 of ARCHITECTURE_REVIEW.md):
#       creating the engine at import bound every importer to the env-derived
#       URL before a test could substitute its own — see brain/db/session.py
#       for the full rationale; this file mirrors it.
#
# HOW: `async with get_session() as session: ...` — commits on clean exit,
#      rolls back and re-raises on exception. Tests pass their own sessionmaker
#      via the `factory` argument / a store's session_factory.
# =============================================================================

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agents.kaya.config import settings


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
    """Session factory over an engine (expire_on_commit=False — ORM objects
    stay readable after commit)."""
    return async_sessionmaker(engine, expire_on_commit=False)


_default_engine: AsyncEngine | None = None
_default_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def default_engine() -> AsyncEngine:
    """The engine for Кая's own database (lazy singleton)."""
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


@asynccontextmanager
async def get_session(
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[AsyncSession]:
    """Transactional scope for a series of operations against Кая's DB.
    `factory` overrides the process default (tests: a scratch database)."""
    session = (factory or default_sessionmaker())()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
