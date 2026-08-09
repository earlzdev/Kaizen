# =============================================================================
# Hub lease sweeper — modules/tracker/sweeper.py
# =============================================================================
# WHAT: A background loop that requeues Directives whose holder stopped
#       heartbeating. Every N seconds it asks the store for expired leases and
#       puts each one back to `queued`; the dispatcher then hands it to whoever
#       is alive (architecture §7 case 8).
#
# WHY this exists at all — it closes the v1 dead-claim hole: a project that
#       claimed work and then died left that work `claimed` forever. Nothing
#       noticed, nothing retried, and the owner found out by asking. A lease is
#       simply the project promising "I am still here"; when the promise stops,
#       the work goes back in the queue.
#
# WHY a DB sweep rather than a timer per Directive (same reasoning as Brain's
#       reminder sweeper): a sleeping coroutine dies with the process, so every
#       Hub restart would forget every deadline. The deadline lives in the row;
#       this loop only checks it.
#
# WHY the poller tier is untouched: `store.expired_leases` only returns rows
#       with a NON-NULL lease, and `claim_next` never opens one. Pollers do not
#       heartbeat, so a lease on them would mean "requeue this healthy, slow
#       job every two minutes" — the exact opposite of the intent.
#
# HOW: `LeaseSweeper(on_event).run_forever()` as a background task in main.py.
# =============================================================================

import asyncio
import logging

from modules.tracker import store
from modules.tracker.config import settings
from modules.tracker.hub_grpc import EventHook

logger = logging.getLogger(__name__)


class LeaseSweeper:
    """Requeues Directives whose project stopped heartbeating."""

    def __init__(self, on_event: EventHook | None = None) -> None:
        self._on_event = on_event

    async def run_forever(self) -> None:
        logger.info(
            "Lease sweeper started (every %ds, lease %ds)",
            settings.tracker_sweep_interval_sec, settings.tracker_lease_seconds,
        )
        while True:
            try:
                await self.sweep_once()
            except Exception:
                logger.exception("Lease sweep failed; will retry next tick")
            await asyncio.sleep(settings.tracker_sweep_interval_sec)

    async def sweep_once(self) -> int:
        """Requeue every expired lease. Returns how many were requeued."""
        expired = await store.expired_leases()
        requeued = 0
        for directive in expired:
            try:
                # claimed_by is cleared with the same write: a `queued`
                # Directive still labelled "by anderson" would show a dead agent
                # holding work in the panel.
                await store.set_status(directive.id, "queued", claimed_by="")
            except store.TransitionError as e:
                # It reached a terminal state between the query and this write.
                # Nothing to do — the lease is moot.
                logger.debug("Skipping expired #%s: %s", directive.id, e)
                continue
            requeued += 1
            logger.warning(
                "Lease expired on #%s (%s) — requeued", directive.id, directive.title
            )
            await self._notify(directive)
        return requeued

    async def _notify(self, directive) -> None:
        """Tell the owner their work stalled and is being retried.

        Worth a message, unlike a failed dispatch attempt: a lease expiring
        means a project accepted work and then went silent MID-FLIGHT, which is
        the shape of a crash rather than of a restart.
        """
        text = (
            f"♻️ Задача #{directive.id} «{directive.title}» вернулась в очередь: "
            f"проект перестал отвечать во время работы."
        )
        if self._on_event is None:
            logger.info("[event:lease_expired] %s", text)
            return
        try:
            await self._on_event(
                {"kind": "lease_expired", "text": text, "directive_id": directive.id}
            )
        except Exception:
            logger.exception("Event hook failed for lease_expired")


__all__ = ["LeaseSweeper"]
