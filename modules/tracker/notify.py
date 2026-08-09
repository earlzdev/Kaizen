# =============================================================================
# Owner notifier — modules/tracker/notify.py
# =============================================================================
# WHAT: The `on_event` hook the Hub, the dispatcher and the sweeper call when
#       something happened the owner should hear about: a Directive finished, a
#       project's agent has a question, a new project wants to enroll, work was
#       requeued. One HTTP POST to Brain's /event route.
#
# WHY it goes through BRAIN and not straight to Кая: service isolation. A module
#       must not know which agents exist, where they listen, or the delivery
#       token — Brain already owns the agent registry and the push client, so
#       the module says WHAT happened and Brain decides WHO hears it. Add a
#       second agent later and nothing here changes.
#
# WHY every failure is swallowed: this is the LAST thing that happens after a
#       project's RPC has already been recorded. A Telegram outage must not fail
#       the report that an hour of fleet work produced — the row is written, the
#       panel shows it, and the owner finds out a moment later instead of
#       instantly. Losing the notification is a nuisance; losing the report
#       would be the actual damage.
#
# HOW: `Notifier(brain_url, token).send` is passed as `on_event` to
#      HubServicer / Dispatcher / LeaseSweeper in main.py.
# =============================================================================

import logging

import aiohttp

logger = logging.getLogger(__name__)

# Two hard ceilings sit downstream of this file, and the message most worth
# delivering is the one most likely to hit them: a failed Directive carries the
# build-log tail as its `error` (architecture §7 case 11), and `summary`/`error`
# are unbounded TEXT columns.
#   - Brain's POST /event caps `text` at 8000 chars and answers 400 above it
#     (brain/api_models.py ModuleEventRequest);
#   - when Кая is mid-conversation the same text is relayed straight into ONE
#     Telegram message, which tops out at 4096.
# Neither caller retries, so an over-long event is not delayed — it is LOST.
# Clipping at this single choke point means every producer (report, question,
# epic listing, requeue, offline) arrives shortened instead of not arriving; the
# full text is always in the panel and in `directive_status`.
_MAX_TEXT = 3500
_CLIPPED = "\n… (обрезано — целиком в directive_status)"


class Notifier:
    """Posts the Hub's events to Brain, which pushes them on to the owner's agent."""

    def __init__(self, brain_url: str, token: str, *, timeout: float = 10.0) -> None:
        self._url = brain_url.rstrip("/") + "/event"
        self._tunnel_url = brain_url.rstrip("/") + "/tunnel/message"
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def configured(self) -> bool:
        return bool(self._token)

    async def send(self, event: dict) -> None:
        """Deliver one event. Never raises — see the module header."""
        if not self._token:
            # Fail LOUD in the log but silent to the caller: an unconfigured
            # notifier is a deployment mistake, not a runtime error, and the
            # owner should be able to see it in `make logs`.
            logger.warning(
                "MODULE_EVENT_TOKEN is not set — dropping event '%s': %s",
                event.get("kind"), event.get("text", "")[:120],
            )
            return

        text = event.get("text") or ""
        if not text:
            logger.warning("Dropping a textless '%s' event", event.get("kind"))
            return
        if len(text) > _MAX_TEXT:
            text = text[: _MAX_TEXT - len(_CLIPPED)] + _CLIPPED
        payload = {"kind": event.get("kind", "tracker"), "text": text}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    self._url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                ) as resp:
                    if resp.status >= 400:
                        body = (await resp.text())[:200]
                        logger.warning(
                            "Brain refused event '%s' (HTTP %d): %s",
                            payload["kind"], resp.status, body,
                        )
        except (aiohttp.ClientError, TimeoutError) as e:
            # TimeoutError is caught EXPLICITLY: aiohttp raises it for a total
            # timeout and it is NOT a ClientError subclass, so without this
            # clause a hung Brain would leak the exception into whichever RPC
            # or sweep loop happened to be notifying.
            logger.warning("Could not deliver event '%s': %s", payload["kind"], e)

    async def send_tunnel_message(
        self, directive_id: int, project: str, role: str, text: str, agent_slug: str = ""
    ) -> None:
        """Log one "позови альфреда" turn into Brain's transcript. A SEPARATE
        route from send() above, deliberately: /event clips to a Telegram
        message and fires only on notable events, but every tunnel turn must
        be logged, in full (up to its own, much larger, cap) — see
        brain/api_models.py's TunnelMessageRequest and brain/tunnel.py.
        Never raises, same contract as send()."""
        if not self._token:
            logger.warning(
                "MODULE_EVENT_TOKEN is not set — dropping tunnel message for #%s",
                directive_id,
            )
            return
        payload = {
            "directive_id": directive_id, "project": project, "role": role,
            "text": text, "agent_slug": agent_slug,
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    self._tunnel_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                ) as resp:
                    if resp.status >= 400:
                        body = (await resp.text())[:200]
                        logger.warning(
                            "Brain refused tunnel message for #%s (HTTP %d): %s",
                            directive_id, resp.status, body,
                        )
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning("Could not log tunnel message for #%s: %s", directive_id, e)


__all__ = ["Notifier"]
