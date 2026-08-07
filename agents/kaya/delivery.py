# =============================================================================
# Кая delivery receiver — agents/kaya/delivery.py
# =============================================================================
# WHAT: The tiny HTTP endpoint Brain pushes events to (Phase 6). On POST
#       /deliver it authenticates the shared delivery token, validates the
#       typed DeliveryEvent, and handles it by kind:
#         "reminder"   — RELAY: frame the text for Telegram and send it.
#         "agent_wake" — THINK: a self-reminder Кая left herself fired; run a
#                        full agent turn seeded with the note and send whatever
#                        she decides to say (possibly nothing).
#         "tracker"    — THINK, falling back to RELAY: news from the project
#                        tracker. Same turn machinery as a wake, but if a
#                        conversation is already running (and a second turn
#                        would race it on the history table) the raw text is
#                        relayed instead of refused. A self-note can wait for
#                        the next sweep; "your PR is ready" has no second
#                        chance — POST /event does not retry.
#
# WHY it lives inside Кая's process: she already holds the Telegram bot and the
#       Agent, so the receiver just calls them. It runs alongside aiogram
#       polling (both are asyncio servers) — inbound pushes and outbound
#       polling coexist.
#
# WHY the token check: /deliver is a network endpoint; without auth anyone who
#       reached it could inject messages to the owner. Brain sends
#       `Authorization: Bearer <delivery_token>`; a mismatch is a 401. Fail-safe:
#       an EMPTY configured token rejects everything (can't accidentally run open).
#
# WHY a busy chat gets 503 instead of waiting: a wake-up turn must never run
#       concurrently with a live conversation (they would race on the history
#       table). Waiting inside the handler would just time out Brain's push, so
#       we refuse fast and let Brain's per-reminder exponential backoff bring it
#       back when the conversation is over.
#
# WHY the wake TURN itself runs in the background (202, not 200): Brain's
#       DeliveryClient gives a push 10 seconds. A real agent turn (recall +
#       tools + the self-check gate) takes far longer than that, so answering
#       only after the turn finished meant every wake timed out on Brain's
#       side — the reminder was never marked delivered and the sweeper woke
#       her again, and again, messaging the owner each round. So we claim the
#       chat SYNCHRONOUSLY (the 503-when-busy contract is unchanged), start the
#       turn as a task, and answer immediately. The note is consumed exactly
#       once; failures are logged, not retried (same policy as a crashed turn).
#
# WHY a failed send returns 502: Brain leaves the reminder due and retries, so
#       a transient Telegram hiccup doesn't drop it.
#
# HOW: `build_delivery_app(bot, owner_id, token, agent=..., turns=...)` ->
#       aiohttp.Application, served by main.py on the delivery port.
# =============================================================================

import asyncio
import logging

from aiogram import Bot
from aiohttp import web
from pydantic import ValidationError

from infra.modkit import DeliveryEvent

from agents.kaya.config import settings
from agents.kaya.connector import ChatTurns, send_agent_text
from agents.kaya.strings import t

logger = logging.getLogger(__name__)

# How each event kind reads in Кая's medium (Telegram text). Presentation
# lives HERE, with the agent that talks to the owner — Brain's sweeper sends
# raw text (Step 7 of ARCHITECTURE_REVIEW.md). Resolved lazily (not at import
# time): this module is imported before main() runs the boot-time
# require_language() check, so an eager lookup here would crash on a
# misconfigured KAYA_LANGUAGE with a raw FileNotFoundError instead of that
# check's single clean "missing: ..." message.


def _reminder_prefix() -> str:
    return t(settings.kaya_language, "reminder_prefix")


def _tracker_prefix() -> str:
    return t(settings.kaya_language, "tracker_prefix")


# Typed key for the in-flight wake turns stored on the app (aiohttp wants an
# AppKey, not a bare string). Shutdown — and the tests — await this set.
WAKE_TASKS = web.AppKey("wake_tasks", set)


def _bearer(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    return header[len("Bearer ") :].strip() if header.startswith("Bearer ") else ""


def build_delivery_app(
    bot: Bot,
    owner_id: int,
    token: str,
    agent=None,
    turns: ChatTurns | None = None,
) -> web.Application:
    """Build the receiver app: POST /deliver (token-auth) + GET /health.

    `agent` and `turns` are required only for "agent_wake" events; without
    them a wake is refused (503) rather than silently dropped, so a
    misconfigured wiring shows up in Brain's logs instead of vanishing."""
    turns = turns if turns is not None else ChatTurns()
    # In-flight wake turns (see _handle_wake).
    wake_tasks: set[asyncio.Task] = set()

    async def _relay(text: str) -> web.Response:
        try:
            await bot.send_message(chat_id=owner_id, text=text)
        except Exception:
            logger.exception("Failed to deliver a message to the owner")
            # 502 -> Brain leaves the reminder due and retries next sweep.
            return web.json_response({"error": "telegram send failed"}, status=502)
        return web.json_response({"ok": True})

    async def _handle_reminder(event: DeliveryEvent) -> web.Response:
        return await _relay(_reminder_prefix() + event.text)

    async def _run_turn(seed: str, kind: str) -> None:
        """The self-initiated turn itself, off the request path (see header).
        Nothing it does can be reported back to Brain — the push was already
        answered — so every failure ends here, logged and silent."""
        try:
            reply = (
                await agent.notify(seed) if kind == "tracker" else await agent.wake(seed)
            )
        except Exception:
            # A turn that crashed would crash again next sweep; consume it.
            logger.exception("%s turn failed", kind)
            return
        finally:
            turns.release(owner_id)

        if not reply:
            logger.info("%s turn produced nothing to send — staying quiet", kind)
            return
        try:
            await send_agent_text(bot, owner_id, reply)
        except Exception:
            logger.exception("Failed to deliver a %s message to the owner", kind)

    def _start_turn(seed: str, kind: str) -> web.Response:
        # Strong reference: a bare create_task() may be garbage-collected
        # mid-flight. The set is also what shutdown waits on.
        task = asyncio.create_task(_run_turn(seed, kind))
        wake_tasks.add(task)
        task.add_done_callback(wake_tasks.discard)
        return web.json_response({"ok": True, "started": True}, status=202)

    async def _handle_wake(event: DeliveryEvent) -> web.Response:
        if agent is None:
            logger.error("agent_wake received but no Agent is wired into the receiver")
            return web.json_response({"error": "wake not supported"}, status=503)
        if not turns.claim(owner_id):
            # A conversation is in flight; Brain will bring this back.
            logger.info("agent_wake deferred — a turn is already running")
            return web.json_response({"error": "busy"}, status=503)
        return _start_turn(event.text, "agent_wake")

    async def _handle_tracker(event: DeliveryEvent) -> web.Response:
        """News from the tracker: retell it in her own voice if she can, relay
        it verbatim if she can't.

        The fallback is the whole difference from a wake. A self-note that has
        to wait comes back on the next sweep; a tracker event has no second
        chance — Brain's POST /event is fire-and-forget, so a 503 here would
        simply lose "the PR is ready". Relaying is a worse message than one she
        wrote, and infinitely better than no message.
        """
        if agent is None or not turns.claim(owner_id):
            logger.info("Tracker event relayed verbatim (agent busy or unwired)")
            return await _relay(_tracker_prefix() + event.text)
        return _start_turn(event.text, "tracker")

    async def deliver(request: web.Request) -> web.Response:
        # Fail-safe: an empty configured token rejects everything.
        if not token or _bearer(request) != token:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "body must be JSON"}, status=400)
        try:
            # The typed Brain→agent contract (infra/modkit/events.py): shape
            # mismatch = 400, so a version-skewed Brain shows up in logs, not
            # as a silently garbled message to the owner.
            event = DeliveryEvent.model_validate(body)
        except ValidationError as e:
            logger.warning("Rejected malformed delivery event: %s", e.errors()[0]["msg"])
            return web.json_response({"error": "invalid delivery event"}, status=400)
        if not event.text:
            return web.json_response({"error": "text is required"}, status=400)
        if event.kind == "agent_wake":
            return await _handle_wake(event)
        if event.kind == "tracker":
            return await _handle_tracker(event)
        return await _handle_reminder(event)

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _finish_wakes(app: web.Application):
        """Let a wake turn that is mid-flight finish before the app goes down,
        instead of cancelling it half-written. (Tests await this set too.)"""
        yield
        if wake_tasks:
            await asyncio.gather(*list(wake_tasks), return_exceptions=True)

    app = web.Application()
    app[WAKE_TASKS] = wake_tasks
    app.cleanup_ctx.append(_finish_wakes)
    app.router.add_post("/deliver", deliver)
    app.router.add_get("/health", health)
    return app
