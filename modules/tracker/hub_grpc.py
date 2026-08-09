# =============================================================================
# Hub gRPC service — modules/tracker/hub_grpc.py
# =============================================================================
# WHAT: The Warden-facing face of the tracker (infra/proto/warden.proto, the
#       `Hub` service): Register, PushStatus, PushReport, AskOwner, Heartbeat.
#       Served on its OWN port, so the tracker now has three front doors:
#           :8770  HTTP    — panel + the poller tier
#           :9103  gRPC    — the Module contract (Brain proxies Кая's tools)
#           :9104  gRPC    — this, the Hub contract (projects' Wardens)
#
# WHY a third port rather than folding this into the Module servicer: they are
#       different contracts with different callers and different trust. The
#       Module port is dialed only by Brain, which has already authenticated an
#       agent. This port is dialed by project containers that authenticate
#       THEMSELVES, with a per-project token. One port, one trust model.
#
# THE ONE SECURITY RULE, applied on every RPC below: resolve the token to a
#       project FIRST, then refuse any directive_id that project does not own.
#       Not "check the token and trust the id" — the id is attacker-controlled
#       and the ownership check is what makes a stolen id useless. This is the
#       same guarantee the v1 HTTP report path gave, extended to five RPCs.
#
# THE ONE SUBTLE PART — AskOwner holds its RPC open:
#       An agent asking the owner a question blocks until answered, so this
#       servicer records the Question, marks the Directive `blocked`, and then
#       POLLS the row until an answer lands or the ceiling elapses. Three things
#       make that safe rather than a leak:
#         1. a hard server-side ceiling (TRACKER_QUESTION_MAX_SEC) — a crashed
#            Warden can never pin a handler forever;
#         2. keepalive on both ends (infra/wardenkit CHANNEL_OPTIONS), or an
#            idle network path silently drops a connection both sides believe in;
#         3. a timeout returns `answered=False`, which is a NORMAL outcome the
#            overseer handles (architecture §7 case 5), not an error;
#         4. a Warden that hangs up mid-wait still gets its Directive taken out
#            of `blocked` — that cleanup has to survive the cancellation of the
#            very handler doing it, or the Directive sits blocked forever while
#            the fleet that asked carries on working.
#       Polling — rather than an in-process asyncio.Event — because the answer
#       may arrive at a DIFFERENT process: the owner can answer through Кая
#       (Module port) or the panel (HTTP port). The database is the only thing
#       all three faces share.
#
# HOW: `HubServicer(on_event=...)` added to a grpc.aio server in main.py. The
#      `on_event` hook is how the Hub reaches the owner (Step 5 wires it to
#      Brain's delivery push); with no hook it simply logs.
# =============================================================================

import asyncio
import logging
from collections.abc import Awaitable, Callable

import grpc

from infra.proto.gen import warden_pb2, warden_pb2_grpc
from modules.tracker import store
from modules.tracker.config import settings
from modules.tracker.models import AGENT_STATUS_STATES, DIRECTIVE_STATUSES

logger = logging.getLogger(__name__)

# Agent Status values that mean "this fleet is actually working". Seeing one of
# these is what promotes a Directive from `dispatched` to `running` — the
# project never says "I am running" in so many words, it just starts reporting.
_WORKING_STATES = ("pending", "in_progress", "review", "done")

# Every string below lands in a VARCHAR column, and Postgres does not truncate —
# it raises. So an over-long `phase` (free-form: an agent writes whatever
# describes its pipeline step) would turn an honest status update into a failed
# RPC and a traceback in the Hub's log. The identity-ish fields are clipped, not
# rejected, because losing the tail of a slug is a far better outcome for the
# owner than losing the update; only `name` is refused, because two projects
# clipped to the same name would be one project.
_MAX_NAME = 255      # tracker_projects.name / tracker_agents.name
_MAX_LABEL = 255     # slugs, roles, models, phases, addresses, task ids
_MAX_STATE = 32      # tracker_agent_status.state

EventHook = Callable[[dict], Awaitable[None]]
# (directive_id, project, role, text, agent_slug) -> None. `role` is
# "owner" | "agent" — see brain/tunnel.py, the receiving end.
TunnelLogHook = Callable[[int, str, str, str, str], Awaitable[None]]

# Strong references to fire-and-forget cleanups (see _detach).
_BACKGROUND: set[asyncio.Task] = set()


def _clip(value: str, limit: int = _MAX_LABEL) -> str:
    """Fit an attacker-supplied string into its column."""
    return (value or "")[:limit]


def _detach(coro) -> None:
    """Run `coro` outside the caller's cancellation scope.

    Used for the one write that must still happen when the RPC we are serving
    is being torn down: awaiting it inline would be cancelled along with us.
    The set membership is load-bearing — asyncio keeps only a WEAK reference to
    a running task, so a bare create_task() can be garbage-collected before it
    ever reaches the database.
    """
    task = asyncio.ensure_future(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def _bearer(context: grpc.aio.ServicerContext) -> str:
    """The project token from gRPC metadata ('' if absent).

    Metadata, not a message field: every Warden→Hub call carries it, so a field
    on each message would be noise that is easy to forget on a new RPC.
    """
    for key, value in context.invocation_metadata() or ():
        if key.lower() == "authorization":
            return value[len("Bearer ") :].strip() if value.startswith("Bearer ") else value.strip()
    return ""


def _metadata_value(context: grpc.aio.ServicerContext, name: str) -> str:
    for key, value in context.invocation_metadata() or ():
        if key.lower() == name:
            return value.strip()
    return ""


class HubServicer(warden_pb2_grpc.HubServicer):
    """Serves the Hub contract to projects' Wardens."""

    def __init__(
        self, on_event: EventHook | None = None, on_tunnel_message: TunnelLogHook | None = None
    ) -> None:
        self._on_event = on_event
        self._on_tunnel_message = on_tunnel_message

    async def _log_tunnel_message(
        self, directive_id: int, project: str, role: str, text: str, agent_slug: str = ""
    ) -> None:
        """Direct-but-logged (the owner's own decision): the tunnel's dialogue
        goes straight through, this only writes the transcript. Never raises —
        same reasoning as _notify: a lost transcript row is a nuisance, never a
        reason to fail the RPC that carried the words."""
        if self._on_tunnel_message is None:
            return
        try:
            await self._on_tunnel_message(directive_id, project, role, text, agent_slug)
        except Exception:
            logger.exception("Tunnel log failed for #%s", directive_id)

    # -- owner notification ------------------------------------------------
    async def _notify(self, kind: str, text: str, **extra) -> None:
        """Tell the owner something happened, via whatever hook is wired up.

        Never raises: a delivery failure must not fail the project's RPC. The
        project has done its part correctly, and the Hub's row is already
        written — losing the notification is a nuisance, losing the report is a
        lost afternoon of fleet work.
        """
        event = {"kind": kind, "text": text, **extra}
        if self._on_event is None:
            logger.info("[event:%s] %s", kind, text)
            return
        try:
            await self._on_event(event)
        except Exception:
            logger.exception("Event hook failed for %s", kind)

    # -- auth --------------------------------------------------------------
    async def _project(self, context: grpc.aio.ServicerContext):
        """Resolve the caller's token to an ACTIVE project, or abort the RPC.

        UNAUTHENTICATED (not PERMISSION_DENIED) on a bad token is deliberate:
        it is the exact code infra/wardenkit reacts to by clearing its stored
        token and re-enrolling, which is the self-healing path CLAUDE.md
        describes for every credential in this system.
        """
        token = _bearer(context)
        project = await store.get_project_by_token(token)
        if project is None:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "unknown project token")
        if project.state != "active":
            await context.abort(
                grpc.StatusCode.PERMISSION_DENIED, f"project is {project.state}"
            )
        return project

    async def _owned_directive(self, context, project, directive_id: int):
        """Fetch a Directive, but only if THIS project owns it.

        A missing Directive and someone else's Directive get the identical
        answer, so a token cannot be used to probe for the existence of another
        project's work.
        """
        directive = await store.get_directive(directive_id)
        if directive is None or directive.project_id != project.id:
            await context.abort(
                grpc.StatusCode.NOT_FOUND, f"no directive #{directive_id} for this project"
            )
        return directive

    # -- Register ----------------------------------------------------------
    async def Register(self, request, context):
        """Enrollment (architecture §5) and manifest refresh, in one RPC.

        The whole decision tree lives in store.register_project — one
        transaction, so two Wardens racing to claim the same approval cannot
        both win.
        """
        name = (request.name or "").strip()
        if not name:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "manifest.name is required")
        if len(name) > _MAX_NAME:
            # Refused rather than clipped: the name IS the project's identity
            # here (the owner approves it by name, Кая delegates by name), and
            # two long names clipped to the same 255 chars would be one project.
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"manifest.name must be at most {_MAX_NAME} characters",
            )

        manifest = {
            "name": name,
            "purpose": request.purpose,
            "description": request.description,
            "kinds": list(request.kinds),
            # Clipped here rather than in sync_roster, so the JSONB manifest and
            # the tracker_agents rows built from it say the same thing.
            "roster": [
                {
                    "slug": _clip(a.slug),
                    "name": _clip(a.name),
                    "role": _clip(a.role),
                    "model": _clip(a.model),
                    "area": _clip(a.area, 64),
                    "tier": _clip(a.tier, 32),
                    "reports_to": _clip(a.reports_to),
                }
                for a in request.roster
            ],
            "max_concurrent": request.max_concurrent,
            "repo_url": request.repo_url,
            "default_branch": request.default_branch,
            "grpc_addr": _clip(request.grpc_addr),
        }

        outcome = await store.register_project(
            name,
            manifest,
            token=_bearer(context) or None,
            # The enrolling Warden's one-time secret. Rides in its own metadata
            # header rather than in the manifest, because the manifest is stored
            # whole as JSONB — a secret in it would be a credential at rest.
            secret=_metadata_value(context, "x-enroll-secret"),
            grpc_addr=_clip(request.grpc_addr),
            purpose=request.purpose,
            description=request.description,
            max_concurrent=request.max_concurrent or 1,
        )

        if outcome.unauthenticated:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, outcome.message)

        project = outcome.project
        if project is not None and not outcome.pending:
            await store.sync_roster(project.id, manifest["roster"])
            # The human the fleet works for tops the chart. Seeded by the Hub
            # rather than declared per project: it is the same person every
            # time, and an org chart that starts at the architect hides who
            # the architect is working for.
            await store.ensure_owner(project.id, settings.tracker_owner_name)

        if outcome.created:
            await self._notify(
                "project_enrollment",
                f"Проект «{name}» просится в трекер: {request.purpose or 'без описания'}",
                project=name,
            )

        logger.info(
            "Register '%s': pending=%s%s", name, outcome.pending,
            " (token issued)" if outcome.token else "",
        )
        return warden_pb2.RegisterAck(
            pending=outcome.pending,
            token=outcome.token,
            project_id=project.id if project else 0,
            message=outcome.message,
        )

    # -- PushStatus --------------------------------------------------------
    async def PushStatus(self, request, context):
        project = await self._project(context)
        directive = await self._owned_directive(context, project, request.directive_id)

        # Validated, not clipped, and for the same reason PushReport validates
        # its own: the panel colours by this value and the promotion below
        # branches on it, so an unrecognised state is a project bug worth
        # naming rather than a string worth storing. ok=False (not an abort)
        # because the CALL was legitimate — only its payload wasn't.
        state = (request.state or "in_progress").strip()
        if state not in AGENT_STATUS_STATES:
            return warden_pb2.Ack(
                ok=False, message=f"state must be one of {list(AGENT_STATUS_STATES)}"
            )

        await store.upsert_agent_status(
            directive.id,
            _clip(request.agent_slug) or "unknown",
            state[:_MAX_STATE],
            role=_clip(request.role) or None,
            progress=request.progress or None,
            blockers=request.blockers or None,
            phase=_clip(request.phase) or None,
        )
        await store.touch_project(project.id)

        # Most Status calls carry no usage — only the one closing out an LLM
        # turn (run_persona_turn) does. A ledger row, not a mirrored field:
        # see TrackerAgentUsage's docstring for why this one is append-only.
        if request.HasField("usage"):
            await store.record_agent_usage(
                directive.id,
                _clip(request.agent_slug) or "unknown",
                phase=_clip(request.phase),
                input_tokens=request.usage.input_tokens,
                output_tokens=request.usage.output_tokens,
                cache_read_tokens=request.usage.cache_read_tokens,
                cache_write_tokens=request.usage.cache_write_tokens,
                cost_usd=request.usage.cost_usd,
            )

        # A project never announces "the Directive is running" — it just starts
        # reporting agent Status. The first sign of work is what promotes it.
        if directive.status == "dispatched" and state in _WORKING_STATES:
            try:
                await store.set_status(directive.id, "running")
            except store.TransitionError:
                # Raced with a report that already moved it on. The status row
                # is written either way, which is what this RPC was for.
                pass

        return warden_pb2.Ack(ok=True)

    # -- PushReport --------------------------------------------------------
    async def PushReport(self, request, context):
        project = await self._project(context)
        directive = await self._owned_directive(context, project, request.directive_id)

        state = (request.state or "").strip()
        if state not in DIRECTIVE_STATUSES:
            return warden_pb2.Ack(
                ok=False, message=f"state must be one of {list(DIRECTIVE_STATUSES)}"
            )

        artifacts = [{"type": a.type, "url": a.url} for a in request.artifacts]
        try:
            updated = await store.report_directive(
                directive.id,
                project.id,
                status=state,
                summary=request.summary or None,
                artifacts=artifacts or None,
                error=request.error or None,
                task_id=_clip(request.task_id) or None,
            )
        except store.TransitionError as e:
            # ok=False rather than an aborted RPC: the project's message was
            # well-formed, it just doesn't fit the Directive's state. The kit
            # logs it and moves on instead of retrying something that can never
            # succeed.
            logger.warning("Report for #%s refused: %s", directive.id, e)
            return warden_pb2.Ack(ok=False, message=str(e))

        await store.touch_project(project.id)
        if updated is not None:
            await self._queue_children(project, updated, request.children)
            # `directive` was read BEFORE the write, so this is the honest test
            # for "did anything actually change". A project retrying a report
            # the Hub already recorded (case 9) is a same-state no-op, and
            # announcing it again would tell the owner "✅ PR ready" twice for
            # one PR — the fastest way to teach them to ignore the channel.
            if updated.status != directive.status:
                await self._announce_report(project, updated, artifacts)
        return warden_pb2.Ack(ok=True)

    async def _queue_children(self, project, parent, children) -> None:
        """Queue an epic's decomposition (architecture §7 case 13).

        The owner chose to let these run unattended, so they go straight into
        the queue — but they are still ORDINARY Directives underneath: the
        dispatcher respects the project's capacity, `cancel_directive` works on
        each one, and the parent id is recorded so the owner can see where a
        piece came from. The one thing the project cannot do is create work
        without telling anyone, which is exactly what routing it through here
        prevents.

        Only an `epic` may come back with children. If ANY Directive could
        attach them, a project would effectively be able to CREATE work rather
        than only be given it — and "a project may only ever be given work" is
        the authority this whole direction exists to keep on the Hub's side.
        """
        if not children:
            return
        if parent.kind != "epic":
            logger.warning(
                "Ignoring %d child directive(s) on #%s: only an epic may decompose "
                "(this one is '%s')", len(children), parent.id, parent.kind,
            )
            return
        specs = [
            {"title": _clip(c.title, 500), "intent": c.intent, "kind": _clip(c.kind, _MAX_STATE)}
            for c in children
        ]
        created = await store.create_children(parent.id, specs)
        if not created:
            return
        listing = "\n".join(f"• #{d.id} {d.title}" for d in created)
        await self._notify(
            "epic_decomposed",
            f"🧩 [{project.name} #{parent.id}] «{parent.title}» разбита на "
            f"{len(created)} задач(и), они уже в очереди:\n{listing}",
            project=project.name, directive_id=parent.id,
        )

    async def _announce_report(self, project, directive, artifacts: list[dict]) -> None:
        """Tell the owner about the outcomes that are actually theirs to act on.

        `running` is not one of them — a fleet reporting progress is not news,
        and a Telegram message per phase change would train the owner to ignore
        the channel that also carries "your PR is ready".
        """
        if directive.status not in ("done", "failed", "cancelled", "review", "blocked"):
            return
        links = " ".join(a.get("url", "") for a in artifacts if a.get("url"))
        icon = {"done": "✅", "failed": "❌", "cancelled": "🚫",
                "review": "👀", "blocked": "⏸"}.get(directive.status, "•")
        text = (
            f"{icon} [{project.name} #{directive.id}] {directive.title} — {directive.status}"
            + (f"\n{directive.summary}" if directive.summary else "")
            + (f"\n{links}" if links else "")
            + (f"\nerror: {directive.error}" if directive.error else "")
        )
        await self._notify(
            "directive_report", text,
            project=project.name, directive_id=directive.id, state=directive.status,
        )

    # -- AskOwner ----------------------------------------------------------
    async def AskOwner(self, request, context):
        """Record a Question, block the Directive, and hold this RPC open.

        See the module header for why the wait is a poll and why a timeout is a
        normal outcome rather than an error.
        """
        project = await self._project(context)
        directive = await self._owned_directive(context, project, request.directive_id)

        text = (request.text or "").strip()
        if not text:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "question text is required")

        # The ceiling is the Hub's, not the caller's: a Warden asking for a
        # 10-hour timeout must not be able to hold a server handler that long.
        # Decided BEFORE the row is written, so the row records how long anyone
        # will actually wait for an answer.
        #
        # Every hostile shape of an int32 has to land somewhere sane: huge is
        # capped by the ceiling, 0 ("unset", the proto3 default) means "use the
        # ceiling", and NEGATIVE — which min() would happily pass through — is
        # treated the same as 0. Letting a negative through would create a
        # question that is born expired: the owner is notified, the Directive
        # flips to `blocked` and straight back, and the answer they type reaches
        # nobody.
        ceiling = max(1, settings.tracker_question_max_sec)
        asked = request.timeout_sec if request.timeout_sec > 0 else ceiling
        wait_for = max(1, min(asked, ceiling))

        question = await store.create_question(
            directive.id, _clip(request.agent_slug) or "unknown", text,
            list(request.suggested), ttl_seconds=wait_for,
        )
        try:
            await store.set_status(directive.id, "blocked")
        except store.TransitionError as e:
            # Terminal Directives can't block. The question row stays for the
            # record, but nobody is waiting on it.
            logger.warning("Cannot block #%s for a question: %s", directive.id, e)
            return warden_pb2.Answer(answered=False, question_id=question.id)

        suggestions = "\n".join(f"• {s}" for s in request.suggested)
        # The question id has to be in the TEXT, not only in the event's extra
        # fields: `text` is the ONLY thing that survives the whole path to the
        # agent (Notifier -> Brain /event -> DeliveryEvent, all of which carry
        # kind+text and nothing else). Without it Кая is told to answer a
        # question whose id she was never given, and `answer_question` — which
        # takes exactly that id — becomes uncallable.
        await self._notify(
            "question",
            f"❓ [{project.name} #{directive.id}] {request.agent_slug} "
            f"спрашивает (вопрос #{question.id}):\n{text}"
            + (f"\n\n{suggestions}" if suggestions else ""),
            project=project.name, directive_id=directive.id, question_id=question.id,
        )

        try:
            answered = await self._await_answer(question.id, wait_for)
        except asyncio.CancelledError:
            # The Warden hung up while we held the RPC — its container was
            # restarted, its client deadline fired, or the Hub itself is
            # shutting down. grpc.aio delivers that as a cancellation of THIS
            # coroutine, and the naive shape (cleanup after the await) would
            # never run: the Directive would stay `blocked` for good, while the
            # fleet that asked carries on working, because ask_owner treats a
            # dropped RPC exactly like "no answer" and resumes. Detached,
            # because we are being torn down and an inline await would be
            # cancelled with us.
            _detach(self._unblock(directive.id))
            raise

        # Either way the agent resumes, so the Directive stops being blocked —
        # on a timeout it proceeds under a stated assumption or reports failed
        # (case 5), and both of those are `running` work.
        await self._unblock(directive.id)

        if answered is None:
            logger.info("Question #%s timed out after %ds", question.id, wait_for)
            return warden_pb2.Answer(answered=False, question_id=question.id)
        return warden_pb2.Answer(
            answered=True, answer=answered.answer or "", question_id=question.id
        )

    async def _unblock(self, directive_id: int) -> None:
        """Take a Directive out of `blocked` now that nobody is waiting on it.

        Swallows everything: this runs on the way out of AskOwner (sometimes
        while that RPC is being cancelled), and there is no caller left to tell.
        """
        try:
            await store.set_status(directive_id, "running")
        except store.TransitionError:
            pass  # cancelled or finished while we waited — leave it be
        except Exception:
            logger.exception("Could not unblock directive #%s", directive_id)

    async def _await_answer(self, question_id: int, timeout_sec: int):
        """Poll the question row until it is answered or the ceiling elapses.

        Returns the answered TrackerQuestion, or None on timeout. Polling
        rather than an in-process event because the answer arrives through a
        different face of this service (Кая's tool call, or the panel) — see
        the module header.
        """
        interval = max(1, settings.tracker_question_poll_sec)
        waited = 0
        while waited < timeout_sec:
            await asyncio.sleep(min(interval, timeout_sec - waited))
            waited += interval
            question = await store.get_question(question_id)
            if question is not None and question.answered_at is not None:
                return question
        return None

    # -- Heartbeat ---------------------------------------------------------
    async def Heartbeat(self, request, context):
        project = await self._project(context)
        directive = await self._owned_directive(context, project, request.directive_id)
        extended = await store.touch_lease(
            directive.id, project.id, settings.tracker_lease_seconds
        )
        await store.touch_project(project.id)
        if extended is None:
            # Not an abort: the Warden is alive and correct to be beating; the
            # Directive simply isn't in a leased state any more (it finished,
            # or the owner cancelled it). ok=False tells the kit to stop.
            return warden_pb2.Ack(
                ok=False, message=f"directive #{directive.id} is {directive.status}"
            )
        return warden_pb2.Ack(ok=True)

    # -- PushChatMessage (the "позови альфреда" tunnel) ---------------------
    async def PushChatMessage(self, request, context):
        """One agent reply pushed up mid-conversation.

        Mirrors PushStatus, not AskOwner: fire from the Warden, no blocking,
        no RPC held open. A `converse` Directive has no single "the answer"
        moment to wait for — every turn is just news, delivered the same way
        a status update is.
        """
        project = await self._project(context)
        directive = await self._owned_directive(context, project, request.directive_id)
        if directive.kind != "converse":
            return warden_pb2.Ack(
                ok=False, message=f"directive #{directive.id} is not a conversation"
            )

        text = (request.text or "").strip()
        if not text and not request.closed:
            return warden_pb2.Ack(ok=False, message="text is required")

        # A turn is a sign of life, same as a Heartbeat — a conversation must
        # not be swept out from under the owner just because they paused to
        # think between messages.
        await store.touch_lease(directive.id, project.id, settings.tracker_lease_seconds)
        await store.touch_project(project.id)

        if text:
            await self._notify(
                "converse",
                f"💬 [{project.name} #{directive.id}] {request.agent_slug or 'agent'}: {text}",
                project=project.name, directive_id=directive.id,
                agent_slug=request.agent_slug,
            )
            await self._log_tunnel_message(
                directive.id, project.name, "agent", text, request.agent_slug
            )

        if request.closed:
            try:
                await store.set_status(
                    directive.id, "done", summary="conversation ended by the agent"
                )
            except store.TransitionError:
                pass  # the owner already ended it (Cancel) — nothing to do

        return warden_pb2.Ack(ok=True)


async def serve_hub(
    bind_addr: str,
    on_event: EventHook | None = None,
    on_tunnel_message: TunnelLogHook | None = None,
) -> grpc.aio.Server:
    """Start the Hub gRPC server and return it (already started).

    Keepalive options mirror infra/wardenkit's CHANNEL_OPTIONS: this server
    holds AskOwner calls open for a long time, so both ends must agree that a
    quiet connection is still a live one — and that a ping every 30s is
    friendly rather than abuse worth a GOAWAY.
    """
    server = grpc.aio.server(
        options=[
            ("grpc.keepalive_time_ms", 30_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.max_pings_without_data", 0),
            ("grpc.http2.min_ping_interval_without_data_ms", 10_000),
            # A stale container must never share this port with a fresh one —
            # the kernel would round-robin dispatches between them.
            ("grpc.so_reuseport", 0),
        ]
    )
    warden_pb2_grpc.add_HubServicer_to_server(
        HubServicer(on_event, on_tunnel_message), server
    )
    if server.add_insecure_port(bind_addr) == 0:
        raise RuntimeError(f"Hub could not bind {bind_addr} (in use?)")
    await server.start()
    logger.info("Tracker Hub gRPC on %s", bind_addr)
    return server


__all__ = ["EventHook", "HubServicer", "serve_hub"]
