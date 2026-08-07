# =============================================================================
# Brain DB session factory — brain/db/session.py
# =============================================================================
# WHAT: Engine/sessionmaker FACTORIES for Brain's database, plus a lazy default
#       bound to settings.database_url. `get_session()` is the transactional
#       scope every store uses; a store may be handed an explicit sessionmaker
#       (tests: a scratch database) or fall back to the process-wide default.
#
# WHY factories instead of an import-time engine (Step 3 of
#       ARCHITECTURE_REVIEW.md): `engine = create_async_engine(...)` at module
#       level meant importing ANY store bound it to the env-derived URL before
#       a test (or any caller) could say otherwise — the single biggest
#       testability blocker in the repo. Now nothing touches the network or the
#       config until the first session is actually opened, and a test can hand
#       a store its own sessionmaker without env tricks.
#
# WHY the default is still process-wide and lazy (not passed everywhere): the
#       services construct their object graphs in main.py; forcing every call
#       site to thread a sessionmaker through would be ceremony for a solo
#       repo. The default preserves the old ergonomics; the factory parameter
#       is the new seam.
#
# HOW: production code: `async with get_session() as session: ...` — commits on
#      clean exit, rolls back on exception. Tests: `make_sessionmaker(
#      make_engine(url))` and pass it to a store's session_factory.
# =============================================================================

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from brain.config import settings


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
    stay readable after commit (e.g. to serialise a freshly-minted agent's
    id/slug into a response)."""
    return async_sessionmaker(engine, expire_on_commit=False)


# Lazy process-wide default (created on first use, NOT at import).
_default_engine: AsyncEngine | None = None
_default_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def default_engine() -> AsyncEngine:
    """The engine for Brain's own database (lazy singleton)."""
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
    """Transactional scope for a series of operations against Brain's DB.
    `factory` overrides the process default (how tests point a store at a
    scratch database)."""
    session = (factory or default_sessionmaker())()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
