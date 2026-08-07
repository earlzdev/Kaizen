# =============================================================================
# Integration tests — brain/enroll.py against a SCRATCH database
# =============================================================================
# WHAT: The hardened pairing flow (Step 4 of ARCHITECTURE_REVIEW.md): token
#       minted at claim time, no plaintext at rest, and — the security
#       regression this file exists for — an approved row must never be
#       claimable with a different secret.
# WHY: the old flow stashed the plaintext token on approval AND re-armed the
#       secret on request — anyone who could reach /enroll could steal a
#       pending approval (ARCHITECTURE_REVIEW.md §2.2-2).
# HOW: EnrollmentStore + AgentStore over the shared scratch-DB sessionmaker
#       (conftest.py); no HTTP layer involved.
# =============================================================================

import pytest
from sqlalchemy import select

from brain.agents import AgentStore
from brain.db.models import Enrollment
from brain.enroll import EnrollmentStore


@pytest.fixture
def agents(scratch_sessions) -> AgentStore:
    return AgentStore(session_factory=scratch_sessions)


@pytest.fixture
def enroll(scratch_sessions, agents) -> EnrollmentStore:
    return EnrollmentStore(agents, session_factory=scratch_sessions)


async def _row(scratch_sessions, slug: str) -> Enrollment:
    async with scratch_sessions() as session:
        result = await session.execute(select(Enrollment).where(Enrollment.slug == slug))
        return result.scalar_one()


async def _approve_slug(enroll: EnrollmentStore, slug: str) -> None:
    pending = await enroll.list_pending()
    target = next(e for e in pending if e.slug == slug)
    assert await enroll.approve(target.id)


async def test_happy_path_token_minted_at_claim(enroll, agents, scratch_sessions):
    assert await enroll.request("kaya", "secret-1") == "pending"
    await _approve_slug(enroll, "kaya")

    # No credential at rest between approve and claim.
    assert (await _row(scratch_sessions, "kaya")).token is None

    status, token = await enroll.claim("kaya", "secret-1")
    assert status == "approved"
    assert token
    # The minted token authenticates against the agents registry.
    agent = await agents.authenticate(token)
    assert agent is not None and agent.slug == "kaya"

    # One-time claim: the row is now 'claimed', no second token.
    assert await enroll.claim("kaya", "secret-1") == ("claimed", None)
    assert (await _row(scratch_sessions, "kaya")).token is None


async def test_approved_row_is_not_claimable_with_a_different_secret(
    enroll, agents, scratch_sessions
):
    """The §2.2-2 regression test: a second requester must never inherit an
    approval — the row demotes to pending and the owner decides again."""
    await enroll.request("kaya", "legit-secret")
    await _approve_slug(enroll, "kaya")

    # Attacker re-requests the approved slug with their own secret.
    assert await enroll.request("kaya", "attacker-secret") == "pending"
    # The attacker gets no token — their claim sees a PENDING row.
    assert await enroll.claim("kaya", "attacker-secret") == ("pending", None)
    # The original approval is revoked: the legit secret is unknown now, so
    # the legit agent falls back to re-enrolling (self-healing path).
    assert await enroll.claim("kaya", "legit-secret") == ("unknown", None)
    # And no agent was ever minted along the way.
    assert all(a.slug != "kaya" for a in await agents.list_agents())


async def test_approved_row_survives_a_reboot_with_the_same_secret(enroll):
    """The same agent re-requesting (crash before claim) keeps its approval."""
    await enroll.request("kaya", "secret-1")
    await _approve_slug(enroll, "kaya")
    assert await enroll.request("kaya", "secret-1") == "approved"
    status, token = await enroll.claim("kaya", "secret-1")
    assert status == "approved" and token


async def test_claim_with_wrong_secret_is_unknown(enroll):
    await enroll.request("kaya", "secret-1")
    assert await enroll.claim("kaya", "wrong") == ("unknown", None)
    assert await enroll.claim("nobody", "secret-1") == ("unknown", None)


async def test_rejected_row_yields_no_token_and_can_reenroll(enroll):
    await enroll.request("kaya", "secret-1")
    pending = await enroll.list_pending()
    assert await enroll.reject(pending[0].id)
    assert await enroll.claim("kaya", "secret-1") == ("rejected", None)
    # A fresh request re-arms the row back to pending.
    assert await enroll.request("kaya", "secret-2") == "pending"


async def test_claimed_row_reenrolls_as_pending(enroll):
    """An agent that lost its stored token starts a NEW pairing cycle."""
    await enroll.request("kaya", "secret-1")
    await _approve_slug(enroll, "kaya")
    await enroll.claim("kaya", "secret-1")
    assert await enroll.request("kaya", "secret-2") == "pending"
