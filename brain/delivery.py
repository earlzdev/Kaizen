# =============================================================================
# Brain delivery client — brain/delivery.py
# =============================================================================
# WHAT: Pushes an event to an agent's delivery_addr over HTTP (Phase 6). This is
#       the outbound half of callbacks: Brain -> agent, the opposite direction of
#       the MCP request path (agent -> Brain).
#
# WHY HTTP POST to a per-agent address: the plan puts `delivery_addr` in the
#       agents registry and has Brain "push an event" to it. Each agent runs a
#       tiny receiver at that address; Brain just POSTs a small JSON event.
#
# WHY a shared delivery token: the receiver is a network endpoint, so pushes
#       must be authenticated or anyone could inject messages to the owner. Brain
#       sends `Authorization: Bearer <delivery_token>`; the agent verifies it.
#
# WHY push returns a bool (never raises): the sweeper decides what to do on
#       failure (leave the reminder due so it retries next sweep). A down agent
#       must not crash the sweep loop.
#
# HOW: `await DeliveryClient(token).push(addr, {"kind": "reminder", "text": ...})`.
# =============================================================================

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class DeliveryClient:
    """Posts events to agents' delivery addresses."""

    def __init__(self, token: str, *, timeout: float = 10.0) -> None:
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def push(self, delivery_addr: str, event: dict[str, Any]) -> bool:
        """POST an event to `delivery_addr`. Returns True on a 2xx, False on any
        error (bad status, unreachable, timeout) — logged, never raised."""
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(delivery_addr, json=event, headers=headers) as resp:
                    if resp.status >= 400:
                        body = (await resp.text())[:200]
                        logger.warning("Delivery to %s -> HTTP %d: %s", delivery_addr, resp.status, body)
                        return False
                    return True
        except (aiohttp.ClientError, TimeoutError) as e:
            # TimeoutError is caught EXPLICITLY: aiohttp raises it for a total-
            # timeout and it is NOT a ClientError subclass — without this clause
            # a slow/hung agent would leak the exception into the sweeper and
            # abort the rest of that sweep's reminders.
            logger.warning("Delivery to %s failed: %s", delivery_addr, e or type(e).__name__)
            return False
