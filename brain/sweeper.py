# =============================================================================
# Brain reminder sweeper — brain/sweeper.py
# =============================================================================
# WHAT: A background loop that fires due reminders (Phase 6). Every N seconds it
#       finds reminders whose time has come and pushes them to the owning agent's
#       delivery_addr, then advances/completes each one. Failed pushes back off
#       exponentially per reminder (in-memory) instead of hammering every sweep.
#
# WHY a DB sweep (not asyncio.sleep-until-due): a sleeping coroutine dies with
#       the process — every restart would lose pending reminders. The schedule
#       lives in the DB; this loop just checks it.
#
# WHY it marks delivered ONLY after a successful push (at-least-once): if the
#       agent is down, the reminder stays due and retries — better a late
#       reminder than a lost one. A rare double-delivery (push ok, mark fails)
#       is the acceptable tradeoff.
#
# WHY per-reminder exponential backoff (Step 9): an agent down for a day used
#       to mean ~2,880 retry pushes per reminder at the sweep cadence. Failures
#       now double the wait (interval → 2× → 4× ... capped at _BACKOFF_CAP_S),
#       tracked in memory only — a Brain restart resets the backoff, which is
#       fine: at-least-once still holds, and the DB row is still the schedule.
#
# WHY the fallback target is EXPLICIT config (Step 9): a reminder is delivered
#       to the agent that SET it; if that agent has no address, the old code
#       picked "any agent with an address" — with a second agent registered
#       (Кузя) that misroutes reminders. The fallback is now only the
#       configured default_delivery_agent_slug (compose: kaya), else skip+warn.
#
# HOW: `ReminderSweeper(memory, agents, delivery, interval).run_forever()` is
#       started as a background task in brain/main.py.
# =============================================================================

import asyncio
import datetime
import logging
import time

from infra.modkit import DeliveryEvent

from brain.agents import AgentStore
from brain.db.models import AUDIENCE_AGENT, AUDIENCE_OWNER
from brain.delivery import DeliveryClient
from brain.memory import MemoryStore

logger = logging.getLogger(__name__)

# Backoff ceiling: a down agent is retried at most this rarely. Low enough
# that reminders arrive within half an hour of the agent's return.
_BACKOFF_CAP_S = 1800.0


class ReminderSweeper:
    """Fires due reminders by pushing them to agents' delivery addresses."""

    def __init__(
        self,
        memory: MemoryStore,
        agents: AgentStore,
        delivery: DeliveryClient,
        interval_seconds: int,
        default_delivery_slug: str = "",
        clock=time.monotonic,
    ) -> None:
        self._memory = memory
        self._agents = agents
        self._delivery = delivery
        self._interval = interval_seconds
        self._default_slug = default_delivery_slug
        # Injectable monotonic clock — tests advance time without sleeping.
        self._clock = clock
        # reminder_id -> (consecutive_failures, monotonic time of next attempt).
        # In-memory on purpose (see header). Pruned as reminders leave the due set.
        self._backoff: dict[int, tuple[int, float]] = {}

    async def run_forever(self) -> None:
        """Sweep on a fixed interval, forever. One iteration's error never stops
        the loop — the next sweep tries again."""
        logger.info("Reminder sweeper started (every %ds)", self._interval)
        while True:
            try:
                await self.sweep_once()
            except Exception:
                logger.exception("Reminder sweep failed; will retry next tick")
            await asyncio.sleep(self._interval)

    async def sweep_once(self) -> int:
        """Deliver every due reminder (that isn't backing off). Returns how many
        were delivered."""
        now = datetime.datetime.now(datetime.timezone.utc)
        due = await self._memory.due_reminders(now)
        # Prune backoff state for reminders that left the due set (delivered
        # earlier, cancelled, advanced) so the dict can't grow forever.
        due_ids = {r.id for r in due}
        for rid in list(self._backoff):
            if rid not in due_ids:
                del self._backoff[rid]

        delivered = 0
        mono = self._clock()
        for reminder in due:
            failures, retry_at = self._backoff.get(reminder.id, (0, 0.0))
            if mono < retry_at:
                continue  # backing off — this sweep leaves it alone
            # One reminder's failure must never abort the rest of the batch.
            try:
                audience = getattr(reminder, "audience", AUDIENCE_OWNER)
                addr = await self._resolve_addr(reminder.agent_id, audience)
                if addr is None:
                    logger.warning(
                        "Reminder %d is due but has no delivery target — skipping",
                        reminder.id,
                    )
                    continue
                # RAW text, typed event (Step 7): how a reminder READS is the
                # receiving agent's call — Кая adds her own "⏰ Напоминание:"
                # framing in her delivery receiver.
                # The audience decides the KIND: an owner reminder is relayed,
                # a self-reminder wakes the agent for a real turn instead.
                kind = "agent_wake" if audience == AUDIENCE_AGENT else "reminder"
                event = DeliveryEvent(kind=kind, text=reminder.text)
                ok = await self._delivery.push(addr, event.model_dump())
                if ok:
                    await self._memory.mark_reminder_delivered(reminder.id)
                    self._backoff.pop(reminder.id, None)
                    delivered += 1
                else:
                    self._note_failure(reminder.id, failures)
            except Exception:
                logger.exception("Reminder %d failed this sweep; will retry", reminder.id)
                self._note_failure(reminder.id, failures)
        if delivered:
            logger.info("Delivered %d reminder(s)", delivered)
        return delivered

    def _note_failure(self, reminder_id: int, prior_failures: int) -> None:
        """Record one failed push: double the wait before the next attempt."""
        failures = prior_failures + 1
        delay = min(self._interval * (2.0 ** failures), _BACKOFF_CAP_S)
        self._backoff[reminder_id] = (failures, self._clock() + delay)
        logger.warning(
            "Reminder %d delivery failed (attempt %d) — next try in %.0fs",
            reminder_id, failures, delay,
        )

    async def _resolve_addr(
        self, agent_id: int | None, audience: str = AUDIENCE_OWNER
    ) -> str | None:
        """The delivery_addr to push a reminder to: the owning agent's, else the
        EXPLICITLY configured default agent's (no arbitrary fallback — Step 9).

        A SELF-NOTE has no fallback at all: an owner reminder is text anyone can
        relay, but waking agent B on agent A's private note would have B run a
        whole turn on a plan it never made and message the owner from it. If the
        author can't be reached, the note waits."""
        if agent_id is not None:
            agent = await self._agents.get_by_id(agent_id)
            if agent is not None and agent.delivery_addr:
                return agent.delivery_addr
        if audience == AUDIENCE_AGENT:
            logger.warning(
                "Self-note is due but its author has no delivery_addr — waiting "
                "(a self-note is never handed to another agent)"
            )
            return None
        if self._default_slug:
            fallback = await self._agents.get_by_slug(self._default_slug)
            if fallback is not None and fallback.delivery_addr:
                return fallback.delivery_addr
            logger.warning(
                "Configured default delivery agent '%s' has no delivery_addr",
                self._default_slug,
            )
        return None
