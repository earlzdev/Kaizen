# =============================================================================
# Agent enrollment client — agents/core/enroll.py
# =============================================================================
# WHAT: The agent side of device pairing. On boot, if the agent has no stored
#       Brain token, it asks Brain to connect (POST /enroll), waits for the owner
#       to approve, then claims its token (POST /enroll/status — the secret rides
#       in the body, never a query string) and stores it. Next boot, it just
#       reuses the stored token — no more pasting tokens into .env.
#
# WHY a CredentialStore seam: WHERE the token is persisted is agent-specific (a
#       file in a volume, a DB row, …). The lib defines the interface + a simple
#       file store; each agent picks where. Reusable across Кая, Кузя, others.
#
# WHY it polls indefinitely: an unapproved agent has nothing to do but wait for
#       the owner's "yes" in the terminal. It logs periodically so you can see
#       it's waiting, and stops on approval (gets token) or rejection (errors).
#
# HOW: `token = await EnrollmentClient(brain_url, slug, enroll_token, store).obtain_token()`.
# =============================================================================

import asyncio
import logging
import os
import secrets
from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol

import aiohttp

logger = logging.getLogger(__name__)


class CredentialStore(Protocol):
    """Where an agent persists its Brain token."""

    def load(self) -> Awaitable[str | None] | str | None: ...
    def save(self, token: str) -> Awaitable[None] | None: ...


class FileCredentialStore:
    """Stores the token in a file (0600). Point it at a path in a mounted volume
    so it survives container recreation."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def load(self) -> str | None:
        if not self._path.is_file():
            return None
        token = self._path.read_text(encoding="utf-8").strip()
        return token or None

    def save(self, token: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Create the file 0600 from the start (a token is a secret) — no window
        # where it briefly exists at the umask default.
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)

    def clear(self) -> None:
        """Drop a stored token (e.g. Brain no longer recognizes it — its DB was
        reset). The next obtain_token() then re-enrolls instead of reusing it."""
        if self._path.is_file():
            self._path.unlink()


class EnrollmentClient:
    """Obtains this agent's Brain token via the enrollment/approval flow."""

    def __init__(
        self,
        brain_url: str,
        slug: str,
        enroll_token: str,
        store: CredentialStore,
        *,
        poll_interval: float = 3.0,
        log_every: float = 30.0,
        timeout: float = 3600.0,
    ) -> None:
        self._base = brain_url.rstrip("/")
        self._slug = slug
        self._enroll_token = enroll_token
        self._store = store
        self._poll = poll_interval
        self._log_every = log_every
        self._timeout = timeout

    async def obtain_token(self) -> str:
        """Return the stored token if present; otherwise enroll, wait for the
        owner's approval, store the issued token, and return it. Raises on
        rejection or if approval doesn't come within `timeout`."""
        existing = self._store.load()
        if asyncio.iscoroutine(existing):
            existing = await existing
        if existing:
            return existing

        secret = secrets.token_urlsafe(32)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Cold-boot friendly: Brain may still be starting (running its own
            # migrations) when the agent first asks — retry connection errors
            # instead of dying and letting docker restart-loop us.
            for attempt in range(60):
                try:
                    async with session.post(
                        self._base + "/enroll",
                        json={"slug": self._slug, "secret": secret,
                              "enroll_token": self._enroll_token},
                    ) as r:
                        if r.status != 200:
                            raise RuntimeError(
                                f"enroll rejected (HTTP {r.status}): {(await r.text())[:200]}"
                            )
                    break
                except aiohttp.ClientConnectionError:
                    if attempt % 10 == 0:
                        logger.info("Brain not reachable yet — waiting to enroll…")
                    await asyncio.sleep(2)
            else:
                raise RuntimeError("Brain never became reachable to enroll")
            logger.info("Enrollment requested as '%s' — waiting for the owner to approve…", self._slug)

            waited = 0.0
            since_log = 0.0
            while waited < self._timeout:
                await asyncio.sleep(self._poll)
                waited += self._poll
                since_log += self._poll
                async with session.post(
                    self._base + "/enroll/status",
                    json={"slug": self._slug, "secret": secret},
                ) as r:
                    data = await r.json()
                status = data.get("status")
                if status == "approved" and data.get("token"):
                    token = data["token"]
                    res = self._store.save(token)
                    if asyncio.iscoroutine(res):
                        await res
                    logger.info("Approved — token stored. '%s' is connected.", self._slug)
                    return token
                if status == "rejected":
                    raise RuntimeError(f"enrollment of '{self._slug}' was rejected")
                if since_log >= self._log_every:
                    since_log = 0.0
                    logger.info("Still waiting for approval of '%s'…", self._slug)

        raise RuntimeError(f"enrollment of '{self._slug}' not approved within {self._timeout:.0f}s")
