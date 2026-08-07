# =============================================================================
# Brain agent registry — brain/agents.py
# =============================================================================
# WHAT: Authenticates agents by bearer token and mints new ones, against Brain's
#       own `agents` table. Lifted from the v1 app/context/store.py AgentStore.
#
# WHY hash the token: we store only sha256(token), never the plaintext — same
#       reason you never store a raw password. The plaintext is shown ONCE, when
#       the agent is minted; after that only its hash lives in the DB, so a
#       database leak can't be used to impersonate an agent.
#
# WHY no owner resolution (unlike v1): Brain is single-tenant and its memory
#       tables carry no user_id, so there is no "map the owner's Telegram id to
#       a users.id" step. The subject is implicit (the one owner); only the
#       actor (which agent) needs resolving, and that is the token lookup here.
#
# HOW: `await AgentStore().authenticate(token)` -> Agent | None;
#      `await AgentStore().create_agent("kaya")` -> (Agent, plaintext_token).
# =============================================================================

import hashlib
import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from brain.db.models import Agent
from brain.db.session import get_session

logger = logging.getLogger(__name__)


def hash_token(token: str) -> str:
    """sha256 hex of a bearer token — what we store and compare against."""
    return hashlib.sha256(token.encode()).hexdigest()


class AgentStore:
    """Authenticates agents and mints new ones against Brain's DB."""

    def __init__(self, session_factory: async_sessionmaker | None = None) -> None:
        # None = the service's default DB; tests pass a scratch-DB sessionmaker.
        self._sessions = session_factory

    async def authenticate(self, token: str) -> Agent | None:
        """Return the Agent a bearer token belongs to, or None if unknown."""
        if not token:
            return None
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(Agent).where(Agent.token_hash == hash_token(token))
            )
            return result.scalar_one_or_none()

    async def create_agent(
        self, slug: str, *, delivery_addr: str | None = None
    ) -> tuple[Agent, str]:
        """Mint a new agent, returning (agent, plaintext_token).

        The plaintext is returned ONCE — hand it to the agent now, because only
        its hash is kept. Raises ValueError on a duplicate slug so a typo
        doesn't silently create a second identity."""
        async with get_session(self._sessions) as session:
            existing = await session.execute(select(Agent).where(Agent.slug == slug))
            if existing.scalar_one_or_none() is not None:
                raise ValueError(f"Agent '{slug}' already exists")
            token = secrets.token_urlsafe(32)
            agent = Agent(
                slug=slug, token_hash=hash_token(token), delivery_addr=delivery_addr
            )
            session.add(agent)
            await session.flush()  # assign agent.id before the session closes
            logger.info("Minted agent '%s' (id=%d)", slug, agent.id)
            return agent, token

    async def mint_or_rotate(
        self, slug: str, *, delivery_addr: str | None = None
    ) -> tuple[Agent, str]:
        """Create the agent if new, or ROTATE its token if it already exists.
        Returns (agent, plaintext_token). Used by enrollment approval so
        re-approving a slug (e.g. an agent that lost its token) re-pairs it
        cleanly instead of failing on the unique slug."""
        token = secrets.token_urlsafe(32)
        async with get_session(self._sessions) as session:
            result = await session.execute(select(Agent).where(Agent.slug == slug))
            agent = result.scalar_one_or_none()
            if agent is None:
                agent = Agent(slug=slug, token_hash=hash_token(token), delivery_addr=delivery_addr)
                session.add(agent)
                await session.flush()
                logger.info("Minted agent '%s' (id=%d) via enrollment", slug, agent.id)
            else:
                agent.token_hash = hash_token(token)
                if delivery_addr:
                    agent.delivery_addr = delivery_addr
                logger.info("Rotated token for agent '%s' (id=%d) via enrollment", slug, agent.id)
            return agent, token

    async def list_agents(self) -> list[Agent]:
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(Agent).order_by(Agent.created_at.asc())
            )
            return list(result.scalars().all())

    async def get_by_id(self, agent_id: int) -> Agent | None:
        """Fetch one agent by id (used to resolve a delivery_addr)."""
        async with get_session(self._sessions) as session:
            return await session.get(Agent, agent_id)

    async def get_by_slug(self, slug: str) -> Agent | None:
        """Fetch one agent by slug (used for the configured delivery fallback)."""
        async with get_session(self._sessions) as session:
            result = await session.execute(select(Agent).where(Agent.slug == slug))
            return result.scalar_one_or_none()

    async def set_delivery_addr(self, agent_id: int, addr: str) -> bool:
        """Set where Brain pushes events to this agent (Phase 6). Returns False
        if the agent doesn't exist."""
        async with get_session(self._sessions) as session:
            agent = await session.get(Agent, agent_id)
            if agent is None:
                return False
            agent.delivery_addr = addr
            logger.info("Agent '%s' delivery_addr set to %s", agent.slug, addr)
            return True
