# =============================================================================
# Brain enrollment — brain/enroll.py
# =============================================================================
# WHAT: The agent device-pairing flow. An agent asks to connect (request), the
#       owner approves it (approve), and the agent claims its token once (claim).
#       Replaces hand-minting a token and pasting it into each agent.
#
# WHY a secret + approval (two gates): the shared ENROLL_TOKEN (checked at the
#       server layer) keeps randoms from even asking. The per-request `secret`
#       (the agent generates it, only its sha256 is stored) ensures ONLY the
#       agent that asked can claim the token. The owner's approval is the human
#       gate — nothing connects without an explicit "yes".
#
# WHY the token is minted AT CLAIM TIME (Step 4 of ARCHITECTURE_REVIEW.md):
#       it used to be minted on approval and stored PLAINTEXT on the enrollment
#       row until claimed — a credential at rest, and re-requesting the slug
#       could re-arm the secret and steal it. Now approve() only flips the
#       status; the token comes into existence inside the first secret-
#       authenticated claim() and goes straight to the caller. Nothing worth
#       stealing is ever stored (only sha256 of the secret).
#
# WHY an approved-but-unclaimed row REFUSES a new secret: re-arming the secret
#       on an approved row was the interception window — anyone who could reach
#       /enroll could take over a pending approval. A claimant with a DIFFERENT
#       secret now demotes the row back to pending (the approval is revoked)
#       and waits for the owner's fresh "yes"; the owner's approval is always
#       the gate.
#
# HOW: request() on POST /enroll; claim() on POST /enroll/status; list_pending()/
#       approve()/reject() behind the admin token. Statuses: pending ->
#       approved -> claimed (terminal until re-request), or rejected.
# =============================================================================

import hashlib
import hmac
import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from brain.agents import AgentStore
from brain.db.models import Enrollment
from brain.db.session import get_session

logger = logging.getLogger(__name__)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _secret_matches(enr: "Enrollment", secret: str) -> bool:
    """Constant-time comparison of a presented secret against the stored hash
    (hmac.compare_digest — a plain != leaks match length via timing)."""
    return hmac.compare_digest(enr.secret_hash, _hash(secret))


class EnrollmentStore:
    """Manages agent connect requests (pairing)."""

    def __init__(
        self, agents: AgentStore, session_factory: async_sessionmaker | None = None
    ) -> None:
        self._agents = agents
        # None = the service's default DB; tests pass a scratch-DB sessionmaker.
        self._sessions = session_factory

    async def request(self, slug: str, secret: str) -> str:
        """An agent asks to connect. Returns the row's resulting status.

        - No row / pending / rejected / claimed: (re)arm as PENDING with THIS
          request's secret — a pending request grants nothing, so the latest
          asker may always take the slot and wait for approval.
        - Approved with the SAME secret: the same agent rebooted mid-wait —
          keep the approval so its next poll claims the token.
        - Approved with a DIFFERENT secret: NEVER inherit the approval (that
          was the interception hole) — demote to pending with the new secret;
          the owner must approve this new claimant explicitly."""
        async with get_session(self._sessions) as session:
            result = await session.execute(select(Enrollment).where(Enrollment.slug == slug))
            enr = result.scalar_one_or_none()
            if enr is None:
                enr = Enrollment(slug=slug, secret_hash=_hash(secret), status="pending", token=None)
                session.add(enr)
                logger.info("Enrollment requested: '%s' (new)", slug)
                return "pending"
            if enr.status == "approved":
                if _secret_matches(enr, secret):
                    return "approved"
                logger.warning(
                    "Enrollment '%s': approved but re-requested with a DIFFERENT "
                    "secret — revoking the approval, back to pending.", slug,
                )
            enr.secret_hash = _hash(secret)
            enr.status = "pending"
            enr.token = None  # hygiene: clear any legacy pre-Step-4 plaintext
            logger.info("Enrollment re-requested: '%s'", slug)
            return "pending"

    async def claim(self, slug: str, secret: str) -> tuple[str, str | None]:
        """Agent polls for its decision. Verifies the secret (constant-time).
        On the first poll after approval the agent token is MINTED right here
        and returned — it exists nowhere at rest; the row becomes 'claimed'.
        Unknown slug/bad secret -> ('unknown', None)."""
        async with get_session(self._sessions) as session:
            result = await session.execute(select(Enrollment).where(Enrollment.slug == slug))
            enr = result.scalar_one_or_none()
            if enr is None or not _secret_matches(enr, secret):
                return "unknown", None
            if enr.status == "approved":
                _, token = await self._agents.mint_or_rotate(enr.slug)
                enr.status = "claimed"
                enr.token = None
                logger.info("Enrollment claimed: '%s' (token minted at claim)", slug)
                return "approved", token
            return enr.status, None

    async def list_pending(self) -> list[Enrollment]:
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(Enrollment).where(Enrollment.status == "pending").order_by(Enrollment.created_at.asc())
            )
            return list(result.scalars().all())

    async def approve(self, enrollment_id: int) -> bool:
        """Approve: flip the status only. The token is NOT minted here — it is
        minted inside the agent's first authenticated claim(), so no credential
        ever waits at rest (and an existing agent's live token isn't rotated
        until the claimant actually proves it holds the secret). Returns False
        if the enrollment doesn't exist."""
        async with get_session(self._sessions) as session:
            enr = await session.get(Enrollment, enrollment_id)
            if enr is None:
                return False
            enr.status = "approved"
            enr.token = None
            logger.info("Enrollment approved: '%s' (token minted on claim)", enr.slug)
            return True

    async def reject(self, enrollment_id: int) -> bool:
        async with get_session(self._sessions) as session:
            enr = await session.get(Enrollment, enrollment_id)
            if enr is None:
                return False
            enr.status = "rejected"
            enr.token = None
            return True


def new_secret() -> str:
    """A fresh enrollment secret (the agent keeps it to claim its token)."""
    return secrets.token_urlsafe(32)
