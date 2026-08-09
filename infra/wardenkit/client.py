# =============================================================================
# Hub client — infra/wardenkit/client.py
# =============================================================================
# WHAT: The project side of the Warden→Hub leg (infra/proto/warden.proto):
#       register / push_status / push_report / ask_owner / heartbeat, with the
#       project token in gRPC metadata, retry with backoff, and self-healing
#       re-enrollment when the Hub says UNAUTHENTICATED.
#
# WHY the kit owns the token file, not each project: the auth model is the same
#       one Кая already uses (ask → owner approves → token issued once, stored
#       in the container's state volume; a rejected or stale token silently
#       re-enrolls). Writing that flow once here means a project's warden.py
#       stays thin and no project invents its own half of it.
#
# WHY there are TWO credentials on disk: `<token_path>` holds the project token
#       (long-lived, issued by the Hub) and `<token_path>.secret` holds the
#       enrollment secret this Warden generated on first boot. The secret is
#       presented only while we have no token, and it is what proves the Warden
#       claiming the owner's approval is the same one that asked for it.
#
# WHY retries differ per RPC — this is the interesting part:
#   push_status / push_report  RETRY. The Hub may be restarting while a
#                              pipeline runs (architecture §7 case 9); the
#                              project must not lose the outcome of an hour of
#                              work because the gateway blinked.
#   heartbeat                  DOES NOT retry. The next beat is seconds away,
#                              and a queue of stale beats proves nothing about
#                              liveness now.
#   ask_owner                  DOES NOT retry. It is a question to a HUMAN: a
#                              retry after a dropped connection would ask the
#                              owner the same thing twice, and a duplicate
#                              Question is worse than an unanswered one — the
#                              overseer handles "no answer" already.
#
# WHY keepalive is configured on the channel: `AskOwner` is a unary RPC the Hub
#       deliberately holds open for as long as the owner takes to answer.
#       Without keepalive, an idle NAT/proxy path can drop a connection that
#       both ends still believe in, and the question dies silently.
#
# HOW:  hub = HubClient("tracker:9104", token_path="/state/hub_token")
#       await hub.enroll(manifest)          # blocks until the owner approves
#       await hub.push_status(...)          # then the pipeline reports freely
# =============================================================================

import asyncio
import logging
import os
import secrets
from pathlib import Path

import grpc

from infra.proto.gen import warden_pb2, warden_pb2_grpc
from infra.wardenkit.clirunner import CliUsage

logger = logging.getLogger(__name__)


def _write_private(path: Path, value: str) -> None:
    """Write a credential to `path`, owner-readable only and atomically.

    Two failure modes this avoids, both of which have bitten the naive version:
    `write_text()` then `chmod()` leaves the secret world-readable for an
    instant, and a container killed mid-write leaves a TRUNCATED credential
    that looks valid, fails auth forever, and needs the owner to go delete it
    by hand. os.replace is atomic within a filesystem, so the file is always
    either the old value or the new one — never half of either.
    """
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)

# Keepalive so a long-held AskOwner survives an idle network path: ping every
# 30s even with no call in flight, and declare the connection dead if a ping
# goes unanswered for 10s.
#
# The last option is the half that is easy to forget, and omitting it breaks
# exactly what the first three are for: a gRPC endpoint tolerates, BY DEFAULT,
# one ping per 5 minutes on a connection carrying no data
# (grpc.http2.min_ping_interval_without_data_ms = 300_000, then 2 strikes and
# out). A 30s keepalive is therefore "abuse" by default, answered with
# GOAWAY(ENHANCE_YOUR_CALM, too_many_pings) — killing the idle-but-alive
# connection the keepalive existed to protect. Both ends of both legs use this
# list, so both ends agree that a ping every 30s is friendly.
CHANNEL_OPTIONS = [
    ("grpc.keepalive_time_ms", 30_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
    ("grpc.http2.min_ping_interval_without_data_ms", 10_000),
]

# Retrying these is worthwhile: the peer is down, restarting, or overloaded,
# and the same call will plausibly succeed a moment later.
_RETRYABLE = (
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
)


class HubUnauthenticated(Exception):
    """The Hub rejected our token — it was revoked, rotated, or never valid.

    The kit clears the stored token before raising, so the caller's correct
    response is simply to `enroll()` again and wait for the owner's approval
    (architecture §7 case 16).
    """


class HubClient:
    """A project's connection to the Kaizen Hub."""

    def __init__(
        self,
        addr: str,
        *,
        token: str | None = None,
        token_path: str | Path | None = None,
        timeout: float = 15.0,
        max_retries: int = 4,
        auto_reenroll: bool = True,
    ) -> None:
        self._addr = addr
        self._token_path = Path(token_path) if token_path else None
        self._token = token or self._load_token()
        # The enrollment secret: generated ONCE, kept beside the token, and
        # presented on every tokenless Register. It is what proves the Warden
        # claiming an approval is the same one that asked for it — without it,
        # whoever calls Register first after the owner clicks "approve" walks
        # away with the project's token. Same guard brain/enroll.py uses for
        # agents; same trust problem, one hop further out.
        self._secret = self._load_or_make_secret()
        self._timeout = timeout
        self._max_retries = max_retries
        self._auto_reenroll = auto_reenroll
        self._channel: grpc.aio.Channel | None = None
        self._stub: warden_pb2_grpc.HubStub | None = None
        # Remembered from the last register/enroll so a rejected token can be
        # healed without the project having to notice or re-supply anything.
        self._manifest: warden_pb2.ProjectManifest | None = None
        self._reenroll_task: asyncio.Task | None = None
        self.project_id: int = 0

    # -- token at rest -----------------------------------------------------
    def _load_token(self) -> str | None:
        if self._token_path and self._token_path.exists():
            value = self._token_path.read_text(encoding="utf-8").strip()
            return value or None
        return None

    @property
    def _secret_path(self) -> Path | None:
        return self._token_path.with_name(self._token_path.name + ".secret") if self._token_path else None

    def _load_or_make_secret(self) -> str:
        """Read this Warden's enrollment secret, creating it on first boot.

        Stable across restarts, which is the whole point: a Warden that
        regenerated its secret on every boot could never claim an approval the
        owner granted while it was down.
        """
        path = self._secret_path
        if path is not None and path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        secret = secrets.token_urlsafe(32)
        if path is not None:
            _write_private(path, secret)
        return secret

    def _store_token(self, token: str | None) -> None:
        self._token = token
        if not self._token_path:
            return
        if token:
            _write_private(self._token_path, token)
        elif self._token_path.exists():
            self._token_path.unlink()

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    # -- plumbing ----------------------------------------------------------
    def _ensure_stub(self) -> warden_pb2_grpc.HubStub:
        if self._stub is None:
            self._channel = grpc.aio.insecure_channel(self._addr, options=CHANNEL_OPTIONS)
            self._stub = warden_pb2_grpc.HubStub(self._channel)
        return self._stub

    def _metadata(self) -> list[tuple[str, str]]:
        """What every call carries: the token once we have one, the enrollment
        secret while we don't.

        Never both — the secret's only job is to claim an approval, and a live
        token means that already happened. Sending it anyway would put a
        long-lived credential on the wire on every heartbeat for no reason.
        """
        if self._token:
            return [("authorization", f"Bearer {self._token}")]
        return [("x-enroll-secret", self._secret)]

    async def close(self) -> None:
        if self._reenroll_task is not None:
            self._reenroll_task.cancel()
            self._reenroll_task = None
        if self._channel is not None:
            await self._channel.close()
            self._channel, self._stub = None, None

    # -- self-healing enrollment (architecture §7 case 16) ------------------
    def _schedule_reenroll(self) -> None:
        """Start re-enrolling in the BACKGROUND after a rejected token.

        Background, not inline: re-enrollment waits on a human ("approve this
        project"), which can take a day. Blocking the call that discovered the
        rejection would hang a running pipeline on the owner's attention, and
        every other call would queue behind it. Instead the failing call fails
        fast, this task waits for the approval, and the moment a new token
        lands every subsequent call carries it. Single-flight: a fleet of
        agents all discovering the rejection at once must not start a dozen
        enrollments.
        """
        if not self._auto_reenroll or self._manifest is None:
            return
        if self._reenroll_task is not None and not self._reenroll_task.done():
            return
        # Kept on `self`, which is what keeps the task alive: asyncio holds only
        # a weak reference, so a bare create_task() can be garbage-collected
        # mid-wait and the project would never re-enroll.
        self._reenroll_task = asyncio.create_task(self._reenroll())

    async def _reenroll(self) -> None:
        try:
            await self.enroll(self._manifest)
            logger.info("Re-enrolled with the Hub after a rejected token")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never propagates: nobody awaits this task. The next rejected call
            # schedules another attempt.
            logger.exception("Re-enrollment failed")

    async def _call(self, rpc_name: str, request, *, timeout=None, retries=None):
        """Invoke one Hub RPC with the token attached and bounded retries.

        Raises HubUnauthenticated on a rejected token (after clearing it), and
        re-raises the last grpc.aio.AioRpcError when the retries run out — the
        caller decides whether that is fatal for its pipeline.
        """
        attempts = self._max_retries if retries is None else retries
        deadline = self._timeout if timeout is None else timeout
        last: Exception | None = None
        for attempt in range(max(1, attempts)):
            stub = self._ensure_stub()
            try:
                return await getattr(stub, rpc_name)(
                    request, timeout=deadline, metadata=self._metadata()
                )
            except grpc.aio.AioRpcError as e:
                if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                    logger.warning("Hub rejected our token on %s — clearing it", rpc_name)
                    self._store_token(None)
                    # Clearing the token alone would leave the project mute
                    # forever: push_status/push_report/heartbeat swallow this
                    # exception, so nothing else would ever notice. Re-enroll.
                    self._schedule_reenroll()
                    raise HubUnauthenticated(e.details() or "unauthenticated") from e
                if e.code() not in _RETRYABLE or attempt == attempts - 1:
                    raise
                last = e
                # 1s, 2s, 4s, 8s — capped so a long outage doesn't turn into an
                # hour-long silence between attempts.
                delay = min(2**attempt, 30)
                logger.warning(
                    "Hub %s failed (%s), retrying in %ds", rpc_name, e.code().name, delay
                )
                await asyncio.sleep(delay)
        raise last if last else RuntimeError(f"{rpc_name} failed with no attempts")

    # -- the RPCs ----------------------------------------------------------
    async def register(self, manifest: warden_pb2.ProjectManifest) -> warden_pb2.RegisterAck:
        """One Register call. Stores the token if this is the call that issues it."""
        # Remembered before the call, not after: a Register that fails is
        # exactly when we most want to know how to re-introduce ourselves.
        self._manifest = manifest
        ack = await self._call("Register", manifest)
        if ack.token:
            self._store_token(ack.token)
            logger.info("Hub issued a project token — enrollment complete")
        if ack.project_id:
            self.project_id = ack.project_id
        return ack

    async def enroll(
        self,
        manifest: warden_pb2.ProjectManifest,
        *,
        poll_seconds: float = 10.0,
        max_wait: float | None = None,
    ) -> warden_pb2.RegisterAck:
        """Register, then keep asking until the owner approves us.

        Returns as soon as we hold a token (a refresh of an existing enrollment
        returns on the first call). Raises TimeoutError if `max_wait` elapses
        while still pending — None, the default, means wait forever, which is
        the right behaviour for a container: the owner may approve tomorrow and
        the project should simply be there when they do.

        An UNREACHABLE Hub is treated the same as a pending approval, and that
        matters more than it looks: compose starts the project's container and
        the Hub at the same moment, so the very first Register normally races a
        Hub that isn't listening yet. Giving up there would crash every Warden
        at boot. A rejected token is also survivable here — it clears itself,
        and the next attempt goes out as a fresh, tokenless enrollment.
        """
        waited = 0.0
        while True:
            try:
                ack = await self.register(manifest)
            except (grpc.aio.AioRpcError, HubUnauthenticated) as e:
                if max_wait is not None and waited >= max_wait:
                    raise
                logger.warning("Hub unreachable during enrollment (%s) — retrying", e)
            else:
                if not ack.pending:
                    return ack
                if max_wait is not None and waited >= max_wait:
                    raise TimeoutError("still awaiting the owner's approval")
                logger.info("Awaiting the owner's approval: %s", ack.message or "pending")
            await asyncio.sleep(poll_seconds)
            waited += poll_seconds

    async def push_status(
        self,
        directive_id: int,
        agent_slug: str,
        state: str,
        *,
        task_id: str = "",
        role: str = "",
        progress: str = "",
        blockers: str = "",
        phase: str = "",
        usage: CliUsage | None = None,
    ) -> bool:
        """Mirror one agent's Status upward. Returns False instead of raising:
        a lost status update is an observability gap, never a reason to abort
        the work it was describing.

        `usage` is set only by the status call that closes out an LLM turn
        (see `run_persona_turn`) — most calls carry none, and a falsy
        `CliUsage` (all zero) is left off the wire entirely so the Hub's
        `HasField("usage")` stays a reliable "did this turn report cost"."""
        update = warden_pb2.StatusUpdate(
            directive_id=directive_id,
            task_id=task_id,
            agent_slug=agent_slug,
            role=role,
            state=state,
            progress=progress,
            blockers=blockers,
            phase=phase,
        )
        if usage:
            update.usage.CopyFrom(warden_pb2.Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_usd=usage.cost_usd,
            ))
        try:
            ack = await self._call("PushStatus", update)
            return ack.ok
        except (grpc.aio.AioRpcError, HubUnauthenticated) as e:
            logger.warning("PushStatus for #%s failed: %s", directive_id, e)
            return False

    async def push_report(
        self,
        directive_id: int,
        state: str,
        *,
        summary: str = "",
        artifacts: list[dict] | None = None,
        error: str = "",
        task_id: str = "",
        children: list[dict] | None = None,
    ) -> bool:
        """Report a Directive's outcome. Retries hard (see the header), but
        still returns a bool rather than raising: by the time this is called the
        work is done, and the caller has nothing useful left to do about it.

        `children` is how an `epic` hands back its decomposition — a list of
        {"title", "intent", "kind"} dicts the Hub queues under this Directive.
        """
        report = warden_pb2.Report(
            directive_id=directive_id,
            state=state,
            summary=summary,
            error=error,
            task_id=task_id,
            artifacts=[
                warden_pb2.Artifact(type=a.get("type", "link"), url=a.get("url", ""))
                for a in (artifacts or [])
            ],
            children=[
                warden_pb2.ChildDirective(
                    title=c.get("title", ""), intent=c.get("intent", ""),
                    kind=c.get("kind", ""),
                )
                for c in (children or [])
            ],
        )
        try:
            ack = await self._call("PushReport", report)
            if not ack.ok:
                logger.warning("Hub refused report for #%s: %s", directive_id, ack.message)
            return ack.ok
        except (grpc.aio.AioRpcError, HubUnauthenticated) as e:
            logger.error("PushReport for #%s failed: %s", directive_id, e)
            return False

    async def push_chat_message(
        self,
        directive_id: int,
        text: str,
        *,
        agent_slug: str = "",
        closed: bool = False,
    ) -> bool:
        """Push one agent reply (or a close notice) up a live conversation.

        Retried like push_status/push_report (default retry policy) — this is
        a push, not a blocking question to a human, so it doesn't share
        ask_owner's no-retry rule. Returns False instead of raising: a lost
        reply is a nuisance for the owner, never a reason to abort the
        conversation loop that produced it."""
        message = warden_pb2.ChatMessage(
            directive_id=directive_id, text=text, agent_slug=agent_slug, closed=closed,
        )
        try:
            ack = await self._call("PushChatMessage", message)
            return ack.ok
        except (grpc.aio.AioRpcError, HubUnauthenticated) as e:
            logger.warning("PushChatMessage for #%s failed: %s", directive_id, e)
            return False

    async def ask_owner(
        self,
        directive_id: int,
        agent_slug: str,
        text: str,
        *,
        timeout_sec: int = 900,
        suggested: list[str] | None = None,
    ) -> warden_pb2.Answer:
        """Ask the owner and BLOCK until they answer or `timeout_sec` elapses.

        Never retries (see the header) and never raises: an unreachable Hub is
        indistinguishable, from the asking agent's point of view, from an owner
        who didn't answer — and the caller already has to handle that case.
        Check `.answered`, not `.answer`: an empty answer is still an answer.
        """
        question = warden_pb2.Question(
            directive_id=directive_id,
            agent_slug=agent_slug,
            text=text,
            timeout_sec=timeout_sec,
            suggested=list(suggested or []),
        )
        try:
            # The client deadline must outlast the server's own ceiling, or we
            # would abandon a question the Hub is still holding open for us.
            return await self._call(
                "AskOwner", question, timeout=timeout_sec + 30, retries=1
            )
        except (grpc.aio.AioRpcError, HubUnauthenticated) as e:
            logger.warning("AskOwner for #%s failed: %s", directive_id, e)
            return warden_pb2.Answer(answered=False)

    async def heartbeat(self, directive_id: int, task_id: str = "") -> bool:
        """Extend this Directive's lease. Never retries, never raises."""
        try:
            ack = await self._call(
                "Heartbeat",
                warden_pb2.HeartbeatPing(directive_id=directive_id, task_id=task_id),
                retries=1,
            )
            return ack.ok
        except (grpc.aio.AioRpcError, HubUnauthenticated) as e:
            logger.debug("Heartbeat for #%s failed: %s", directive_id, e)
            return False


__all__ = ["CHANNEL_OPTIONS", "HubClient", "HubUnauthenticated"]
