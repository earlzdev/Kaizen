# =============================================================================
# Hub dispatcher — modules/tracker/dispatcher.py
# =============================================================================
# WHAT: The outbound half of the Hub. A background loop that hands queued
#       Directives to projects' Wardens over the Hub→Warden gRPC leg
#       (infra/proto/warden.proto): pick the next work, dial, and act on the
#       DispatchAck. Also resyncs with every project after a Hub restart.
#
# WHY the Hub dials OUT at all (the other leg is Warden→Hub): dispatch is the
#       one direction where the Hub knows something the project doesn't, so
#       pushing means a Directive reaches a project the moment it is created
#       instead of one poll interval later. This is also the ONLY leg that
#       breaks if a project ever moves off this machine — see the seam noted in
#       docs/tracker-architecture.md §2.
#
# WHY only projects with a `grpc_addr` are dialed: that is exactly what
#       separates the two integration tiers. A poller-tier project has no
#       Warden to dial and takes its work by asking (store.claim_next); dialing
#       it would be dialing nothing.
#
# THE THREE ANSWERS A WARDEN CAN GIVE, and why each is handled differently:
#   accepted           -> `dispatched` + a lease opens. The project is now on
#                         the hook, and the sweeper will notice if it dies.
#   at_capacity        -> stay `queued`, silently. The project is healthy and
#                         busy; this is not news for anyone.
#   unsupported_kind   -> `failed` immediately, with the reason surfaced. A
#                         Directive nobody can run must not sit in a queue
#                         looking like it is about to happen.
#   (unreachable)      -> stay `queued`, count the attempt, back off. The owner
#                         hears about it only after N attempts, so a container
#                         restart never generates a Telegram message.
#
# HOW: `Dispatcher(on_event).run_forever()` as a background task in main.py.
# =============================================================================

import asyncio
import datetime
import logging

import grpc

from infra.proto.gen import warden_pb2, warden_pb2_grpc
from modules.tracker import store
from modules.tracker.config import settings
from modules.tracker.hub_grpc import EventHook, TunnelLogHook

logger = logging.getLogger(__name__)

# The two rejection reasons the contract gives meaning to; anything else is a
# generic refusal and fails the Directive with whatever text the project sent.
REASON_AT_CAPACITY = "at_capacity"
REASON_UNSUPPORTED_KIND = "unsupported_kind"

# A retry ceiling, so a project that has been down for a day is still tried
# every few minutes rather than once a week.
_BACKOFF_CAP_S = 300

# What one hand-off attempt told us about the PROJECT — which is the only thing
# that decides whether the pass keeps walking down that project's queue:
#   TAKEN    it accepted; we are still inside the capacity computed this pass,
#            so the next Directive is worth offering too.
#   REFUSED  the project is healthy, this particular Directive is not for it.
#            It is `failed` and out of the queue, so the NEXT one may well fit —
#            stopping here would make one bad Directive cost a whole tick.
#   BUSY     no free slot, or nobody home. Nothing else in this queue will fare
#            better before the next pass.
TAKEN = "taken"
REFUSED = "refused"
BUSY = "busy"


def _backoff_seconds(attempts: int) -> int:
    """1, 2, 4, 8 ... capped. Derived from the persisted attempt count rather
    than kept in memory, so a Hub restart doesn't reset a project's backoff and
    hammer it."""
    return min(2 ** max(0, attempts - 1), _BACKOFF_CAP_S)


class Dispatcher:
    """Hands queued Directives to Wardens, one pass at a time."""

    def __init__(
        self, on_event: EventHook | None = None, on_tunnel_message: TunnelLogHook | None = None
    ) -> None:
        self._on_event = on_event
        self._on_tunnel_message = on_tunnel_message
        # One channel per project address, reused across passes: a fresh
        # connection per dispatch would pay the TCP+HTTP/2 handshake every few
        # seconds and defeat the keepalive both ends agreed on.
        self._channels: dict[str, grpc.aio.Channel] = {}

    # -- plumbing ----------------------------------------------------------
    def _stub(self, addr: str) -> warden_pb2_grpc.WardenStub:
        channel = self._channels.get(addr)
        if channel is None:
            channel = grpc.aio.insecure_channel(
                addr,
                options=[
                    ("grpc.keepalive_time_ms", 30_000),
                    ("grpc.keepalive_timeout_ms", 10_000),
                    ("grpc.keepalive_permit_without_calls", 1),
                    ("grpc.http2.max_pings_without_data", 0),
                    ("grpc.http2.min_ping_interval_without_data_ms", 10_000),
                ],
            )
            self._channels[addr] = channel
        return warden_pb2_grpc.WardenStub(channel)

    async def close(self) -> None:
        for channel in self._channels.values():
            await channel.close()
        self._channels.clear()

    async def _notify(self, kind: str, text: str, **extra) -> None:
        if self._on_event is None:
            logger.info("[event:%s] %s", kind, text)
            return
        try:
            await self._on_event({"kind": kind, "text": text, **extra})
        except Exception:
            logger.exception("Event hook failed for %s", kind)

    # -- the loop ----------------------------------------------------------
    async def run_forever(self) -> None:
        """Dispatch on a fixed interval, forever. One pass's error never stops
        the loop — the next pass tries again."""
        logger.info(
            "Dispatcher started (every %ds)", settings.tracker_dispatch_interval_sec
        )
        try:
            await self._resync_all()
        except Exception:
            # The resync is a nice-to-have at boot; the loop below is the whole
            # point of this class. Letting a resync failure escape would end
            # this task before the loop ever starts — and nobody awaits it, so
            # the Hub would simply never dispatch again, silently, until the
            # container was restarted.
            logger.exception("Boot resync failed; starting the loop anyway")
        while True:
            try:
                await self.dispatch_once()
            except Exception:
                logger.exception("Dispatch pass failed; will retry next tick")
            await asyncio.sleep(settings.tracker_dispatch_interval_sec)

    async def dispatch_once(self) -> int:
        """One pass over every Warden-tier project. Returns how many were handed
        over."""
        handed = 0
        for project in await store.list_projects(state="active"):
            if not project.grpc_addr:
                continue  # poller tier — it comes to us
            try:
                handed += await self._drain(project)
            except Exception:
                # One project's bad pass must never cost every other project
                # theirs. Without this the first unexpected error anywhere
                # aborted the whole pass, so a single broken project stalled the
                # entire estate — every tick, for as long as it stayed broken.
                logger.exception("Dispatch pass for '%s' failed", project.name)
        return handed

    async def _drain(self, project) -> int:
        """Offer one project as much of its queue as its free capacity allows."""
        free = project.max_concurrent - await store.count_in_flight(project.id)
        if free <= 0:
            return 0
        handed = 0
        for directive in await store.dispatchable(project.id, free):
            if self._backing_off(directive):
                continue
            try:
                outcome = await self._hand_over(project, directive)
            except Exception:
                # An unexpected failure on ONE Directive — a row protobuf
                # refuses to serialise, a write that lost a race — must not
                # block the queue behind it forever. Count it like a failed
                # attempt so the same poisoned row backs off exponentially
                # instead of being retried (and logged) every few seconds, and
                # move on to the next Directive. Deliberately NOT `_offline`:
                # the project answered us just fine, so the owner must not be
                # told it is down.
                logger.exception(
                    "Hand-off of #%s to '%s' failed", directive.id, project.name
                )
                await store.note_dispatch_failure(directive.id)
                continue
            if outcome == TAKEN:
                handed += 1
            elif outcome == BUSY:
                break
        return handed

    def _backing_off(self, directive) -> bool:
        """True while a previously-failed Directive is still in its cooldown."""
        if not directive.dispatch_attempts:
            return False
        last = directive.updated_at
        if last is None:
            return False
        wait = datetime.timedelta(seconds=_backoff_seconds(directive.dispatch_attempts))
        return datetime.datetime.now(datetime.timezone.utc) < last + wait

    # -- control: the owner aborting work in flight (case 7) ---------------
    async def cancel(self, project, directive, reason: str = "") -> bool:
        """Tell a project to stop working on a Directive. True if it was running.

        Best-effort by contract: the Warden kills its pipeline and deliberately
        leaves the repo and docs/tracker/ files alone so the owner can look at
        what the fleet had done. An unreachable project is NOT a failure to
        cancel — the Hub's own row moves to `cancelled` either way (that write
        belongs to the caller), and the project will find out when its next
        heartbeat is refused.
        """
        if not project.grpc_addr:
            return False  # poller tier: nothing to dial
        try:
            ack = await self._stub(project.grpc_addr).Cancel(
                warden_pb2.CancelRequest(directive_id=directive.id, reason=reason),
                timeout=settings.tracker_dispatch_timeout_sec,
            )
            return bool(ack.accepted)
        except grpc.aio.AioRpcError as e:
            logger.warning(
                "Cancel of #%s could not reach '%s': %s",
                directive.id, project.name, e.code().name,
            )
            return False

    # -- control: the live tunnel ("позови альфреда") ----------------------
    async def deliver_message(self, project, directive_id: int, text: str) -> bool:
        """Push one owner turn into an open `converse` Directive.

        Fire-and-forget by contract (Warden.DeliverMessage only Acks — the
        reply comes back later over Hub.PushChatMessage, same as every other
        Hub->Warden call in this file returns quickly and lets the project's
        own report/push legs carry the actual outcome). False just means the
        Warden could not be reached right now; the caller decides what that
        means for the conversation (retry, tell the owner, etc).
        """
        if not project.grpc_addr:
            return False  # poller tier: nothing to dial
        try:
            ack = await self._stub(project.grpc_addr).DeliverMessage(
                warden_pb2.ChatMessage(directive_id=directive_id, text=text),
                timeout=settings.tracker_dispatch_timeout_sec,
            )
        except grpc.aio.AioRpcError as e:
            logger.warning(
                "DeliverMessage for #%s could not reach '%s': %s",
                directive_id, project.name, e.code().name,
            )
            return False
        if ack.ok and self._on_tunnel_message is not None:
            try:
                await self._on_tunnel_message(directive_id, project.name, "owner", text, "")
            except Exception:
                logger.exception("Tunnel log failed for #%s", directive_id)
        return bool(ack.ok)

    async def restart(self, project, reason: str = "", scope: str = "jobs",
                      requested_by: str = "owner") -> dict:
        """Ask a project to restart itself. Returns what it answered.

        The Hub never restarts anything: it relays a request the project is free
        to refuse (and does refuse, inside its own cooldown — see wardenkit).
        Holding the power to bounce containers on every machine that ever
        registered is not a capability this service should have.

        The directives the project drops come back in `dropped`; requeueing them
        belongs to the caller, which knows whether the owner wanted them retried
        or left alone.
        """
        if not project.grpc_addr:
            return {"accepted": False, "reason": "poller tier: nothing to dial"}
        try:
            ack = await self._stub(project.grpc_addr).Restart(
                warden_pb2.RestartRequest(
                    reason=reason, scope=scope or "jobs", requested_by=requested_by,
                ),
                timeout=settings.tracker_dispatch_timeout_sec,
            )
            result = {
                "accepted": bool(ack.accepted),
                "reason": ack.reason or "",
                "restarting_in_sec": int(ack.restarting_in_sec),
                "dropped": list(ack.dropped_directives),
            }
            logger.info(
                "Restart of '%s' (scope=%s): %s%s",
                project.name, scope, "accepted" if ack.accepted else "refused",
                f" — {ack.reason}" if ack.reason else "",
            )
            return result
        except grpc.aio.AioRpcError as e:
            # Unreachable is the answer, not an error: a Warden whose process is
            # gone cannot restart itself, and the owner needs to hear exactly
            # that rather than a stack trace — the fix is the host's restart
            # policy, not another RPC.
            logger.warning(
                "Restart could not reach '%s': %s", project.name, e.code().name
            )
            return {
                "accepted": False,
                "reason": f"unreachable ({e.code().name}) — the Warden process is "
                          f"not answering; this needs a restart on the host",
                "dropped": [],
            }

    async def _hand_over(self, project, directive) -> str:
        """Dial one project with one Directive. Returns TAKEN / REFUSED / BUSY."""
        message = warden_pb2.Directive(
            id=directive.id,
            kind=directive.kind or "develop",
            intent=directive.description or directive.title,
            title=directive.title,
            task_id=directive.task_id or "",
            priority=directive.priority,
            auto_merge=bool(directive.auto_merge),
        )
        try:
            ack = await self._stub(project.grpc_addr).Dispatch(
                message, timeout=settings.tracker_dispatch_timeout_sec
            )
        except grpc.aio.AioRpcError as e:
            await self._offline(project, directive, e)
            return BUSY

        if ack.accepted:
            try:
                await store.set_status(
                    directive.id,
                    "dispatched",
                    claimed_by=project.name,
                    lease_seconds=settings.tracker_lease_seconds,
                )
            except store.TransitionError as e:
                # It left `queued` between our read and the project's answer —
                # in practice the owner cancelled it while we were dialing. The
                # project is now running work we no longer track, so we stop
                # offering it more this pass: its own at-capacity answer (case
                # 3) is the backstop until the Cancel leg closes the loop.
                logger.error(
                    "'%s' accepted #%s but the Hub could not record it: %s",
                    project.name, directive.id, e,
                )
                return BUSY
            await store.set_task_id(directive.id, ack.task_id)
            await store.clear_dispatch_attempts(directive.id)
            logger.info(
                "Dispatched #%s to '%s' (task '%s')", directive.id, project.name, ack.task_id
            )
            return TAKEN

        if ack.reason == REASON_AT_CAPACITY:
            # Healthy and busy. Our count said otherwise, which just means the
            # project started something between our read and our dial.
            logger.debug("'%s' is at capacity; #%s stays queued", project.name, directive.id)
            return BUSY

        reason = ack.reason or "rejected without a reason"
        try:
            await store.set_status(
                directive.id, "failed", error=f"project rejected: {reason}"
            )
        except store.TransitionError as e:
            # Already terminal (cancelled while we dialed). Nothing left to
            # fail, and nothing to tell the owner about.
            logger.info("Rejected #%s was already settled: %s", directive.id, e)
            return REFUSED
        await self._notify(
            "directive_rejected",
            f"❌ [{project.name} #{directive.id}] {directive.title} — проект отказался: {reason}",
            project=project.name, directive_id=directive.id, reason=reason,
        )
        logger.warning("'%s' rejected #%s: %s", project.name, directive.id, reason)
        return REFUSED

    async def _offline(self, project, directive, error: grpc.aio.AioRpcError) -> None:
        """A Warden we could not reach. Count it, and tell the owner only once
        the silence has gone on long enough to be worth their attention."""
        attempts = await store.note_dispatch_failure(directive.id)
        logger.warning(
            "Could not reach '%s' for #%s (attempt %d): %s",
            project.name, directive.id, attempts, error.code().name,
        )
        if attempts == settings.tracker_dispatch_max_attempts:
            await self._notify(
                "project_offline",
                f"⚠️ Проект «{project.name}» не отвечает — задача #{directive.id} "
                f"«{directive.title}» ждёт в очереди ({attempts} попыток).",
                project=project.name, directive_id=directive.id, attempts=attempts,
            )

    # -- resync after a Hub restart (architecture §7 case 9) ---------------
    async def _resync_all(self) -> None:
        """Ask every Warden what it is actually running, and believe it.

        The Hub's rows are durable, but its idea of "in flight" can be stale
        after a restart: a project may have finished a Directive while we were
        down (its report retried and failed), or crashed and started nothing.
        Health is the cheapest possible reconciliation — one call per project,
        and the answer is authoritative because the Warden is the only party
        that knows what its own processes are doing.
        """
        for project in await store.list_projects(state="active"):
            if not project.grpc_addr:
                continue
            try:
                health = await self._stub(project.grpc_addr).Health(
                    warden_pb2.HealthRequest(), timeout=settings.tracker_dispatch_timeout_sec
                )
            except grpc.aio.AioRpcError as e:
                # Not an error worth shouting about at boot: the project may
                # simply be starting up alongside us. The sweeper will requeue
                # anything it was holding once the lease runs out.
                logger.info(
                    "Resync: '%s' unreachable (%s) — leaving its leases to expire",
                    project.name, e.code().name,
                )
                continue

            running = set(health.running_directives)
            for directive in await store.leased_directives(project.id):
                if directive.id in running:
                    await store.touch_lease(
                        directive.id, project.id, settings.tracker_lease_seconds
                    )
                    continue
                logger.info(
                    "Resync: '%s' is not running #%s — requeueing it",
                    project.name, directive.id,
                )
                try:
                    await store.set_status(directive.id, "queued", claimed_by="")
                except store.TransitionError:
                    pass
            logger.info(
                "Resync: '%s' running %d/%d", project.name, health.running, health.capacity
            )


__all__ = ["Dispatcher"]
