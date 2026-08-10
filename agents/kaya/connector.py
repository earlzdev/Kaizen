# =============================================================================
# Кая Telegram connector — agents/kaya/connector.py
# =============================================================================
# WHAT: The aiogram surface — an owner-only gate + a catch-all handler that turns
#       each incoming text message into agent.reply(text) and sends the reply
#       back. This is the ONLY agent-specific I/O; everything else is Agent Core.
#
# WHY a whitelist middleware (ported from the v1 AuthMiddleware): Кая is a
#       single-owner bot. The middleware runs BEFORE any handler and silently
#       drops anyone not in ALLOWED_USER_IDS — so no handler can ever process a
#       stranger's message, and the bot doesn't reveal it exists.
#
# WHY the handler is thin: the connector owns Telegram I/O only. It does not know
#       about tools, memory or the loop — it hands text to the Agent and returns
#       the Agent's text. "Typing…" is sent while the (possibly multi-tool) turn
#       runs so the owner sees Кая is working.
#
# WHY a per-chat queue (coalescing): the owner often fires several messages in a
#       row. Without a queue aiogram would run a concurrent agent.reply() per
#       message — racing on the history table and answering each fragment out of
#       context. Instead, one worker per chat: messages that arrive while a turn
#       runs are buffered and then enter the model MERGED as a single user turn.
#
# WHY status lines: a deep-research turn (search + read + self-check) can run a
#       minute or more. The agent reports each phase via on_status; the connector
#       turns the interesting ones into short progress messages — but only once
#       the turn is already slow (quick replies stay clean).
#
# WHY errors are caught here: a failed turn must become a friendly message, not a
#       silent non-reply or a stack trace — the owner should always get SOMETHING.
#
# HOW: `build_router(agent, allowed_ids, stt, turns)` -> aiogram Router, included
#      by main.py. `turns` (ChatTurns) is shared with the delivery receiver so a
#      self-reminder wake-up and a live conversation take the chat in turn.
# =============================================================================

import asyncio
import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Message,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
    TelegramObject,
)

from agents.core.agent import Agent
from agents.kaya.config import settings
from agents.kaya.stt import MAX_BYTES, MAX_SECONDS, SpeechKit
from agents.kaya.strings import status_line, t

logger = logging.getLogger(__name__)

# Where incoming photos are saved for the agent to view (CLI Read tool).
_PHOTO_DIR = "/tmp/kaya_photos"
# Purge saved photos older than this on each new save (they're transient).
_PHOTO_TTL_S = 3600

# A direct image URL in the agent's reply -> sent as a real Telegram photo.
_IMG_URL_RE = re.compile(r"https?://\S+\.(?:jpe?g|png|webp|gif)(?:\?\S*)?", re.IGNORECASE)

# Markdown the model habitually emits but Telegram (parse_mode=HTML) renders
# as literal asterisks/brackets. The soul says "plain text only"; this is the
# safety net for when the model forgets.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+")


def _strip_markdown(text: str) -> str:
    """Markdown -> plain text: [title](url) -> 'title — url', **x** -> x,
    '## Heading' -> 'Heading'. Single *asterisks* are left alone (too easy to
    hit legitimate uses)."""
    text = _MD_LINK_RE.sub(r"\1 — \2", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    return _MD_HEADING_RE.sub("", text)


def _split_images(text: str) -> tuple[str, list[str]]:
    """Pull direct image URLs out of a reply. Returns (text_without_them, urls).
    The URLs are removed from the text so the owner gets a photo, not a photo
    PLUS a bare link; empty leftover lines are collapsed."""
    urls = [m.group(0) for m in _IMG_URL_RE.finditer(text)]
    if not urls:
        return text, []
    clean = text
    for u in urls:
        clean = clean.replace(u, "")
    clean = re.sub(r"[ \t]*\n{3,}", "\n\n", clean)          # collapse gaps
    clean = "\n".join(line.rstrip() for line in clean.splitlines()).strip()
    return clean, urls[:5]  # cap: no photo floods


async def _save_incoming_photo(message: Message) -> str:
    """Download the largest size of an incoming photo to _PHOTO_DIR and return
    the path. Also purges stale old photos (transient files, not an archive)."""
    os.makedirs(_PHOTO_DIR, exist_ok=True)
    now = time.time()
    for name in os.listdir(_PHOTO_DIR):
        p = os.path.join(_PHOTO_DIR, name)
        if now - os.path.getmtime(p) > _PHOTO_TTL_S:
            os.remove(p)
    path = os.path.join(_PHOTO_DIR, f"{uuid.uuid4().hex}.jpg")
    await message.bot.download(message.photo[-1], destination=path)
    return path


class OwnerOnlyMiddleware(BaseMiddleware):
    """Drops messages from anyone not in the owner whitelist.

    Registered on the `message` observer, so the event handed to us is a
    `Message`, not an `Update` (that is the level aiogram resolves for this
    observer). We therefore read the acting user from `data['event_from_user']`,
    which aiogram's built-in UserContextMiddleware populates for EVERY event
    type before any observer middleware runs — so this works regardless of the
    concrete event class. (The v1 monolith checked `isinstance(event, Update)`
    because it registered at the update level; doing that here would silently
    pass every stranger straight through to the handler.)"""

    def __init__(self, allowed_ids: list[int]) -> None:
        self._allowed = set(allowed_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return  # no user info -> drop
        if self._allowed and user.id not in self._allowed:
            logger.warning("Unauthorized message from %s (%s)", user.id, user.username)
            return  # silently drop
        return await handler(event, data)


async def _transcribe_voice(message: Message, stt: SpeechKit) -> str | None:
    """Download an incoming voice note and transcribe it. Returns the text, or
    None when it can't be done (not configured / too long / STT failure) —
    the caller has already told the owner why."""
    if not stt.configured:
        await message.answer(t(settings.kaya_language, "voice_not_configured"))
        return None
    voice = message.voice
    # Size cap is generous (chunked STT handles length); it only guards
    # against something absurd arriving as a "voice note".
    if voice.duration > MAX_SECONDS or (voice.file_size or 0) > 4 * MAX_BYTES:
        await message.answer(t(settings.kaya_language, "voice_too_long"))
        return None
    import io

    buf = io.BytesIO()
    await message.bot.download(voice, destination=buf)
    text = await stt.transcribe(buf.getvalue(), voice.duration)
    if text is None:
        await message.answer(t(settings.kaya_language, "voice_transcribe_failed"))
        return None
    if not text:
        await message.answer(t(settings.kaya_language, "voice_no_words"))
        return None
    return text


# Progress messages: stay silent while a turn is still fast (…_AFTER_S), and
# post AT MOST ONE status line per turn (owner's call 2026-07-29: a line per
# search/read step reads as spam) — after that, only the typing indicator.
_STATUS_AFTER_S = 10.0

# Debounce before starting a turn: the owner often types several short
# messages in a burst. Verified against the Bot API docs (2026-07-29): NO
# update type exposes the user's typing/recording status to a bot — that
# exists only for MTProto user clients. So a quiet gap is the only available
# signal that the thought is finished. 4.5s covers typing a short follow-up
# message; the cost is +4.5s before EVERY turn, tune by feel.
_DEBOUNCE_S = 4.5

# How often the message worker re-tries to take a chat that something ELSE
# holds (today: a wake-up turn started by a fired self-reminder). The owner's
# message waits for the wake to finish instead of being dropped.
_TURN_WAIT_S = 1.0


def _status_line(tool: str, detail: str) -> str | None:
    """A short progress line for a phase worth showing the owner — or None
    for internal tools (memory, reminders) that would just be noise."""
    if tool in ("read_page", "WebFetch"):
        detail = re.sub(r"^https?://(www\.)?", "", detail).split("/")[0]
    return status_line(settings.kaya_language, tool, detail)


_REPLY_SNIPPET_MAX = 300


def _forward_sender_label(message: Message) -> str | None:
    """A human-readable "who this was originally from" for a forwarded
    message, or None if it isn't a forward. Covers all four MessageOrigin
    variants (Bot API 7.0+ / aiogram 3.4+)."""
    origin = message.forward_origin
    if origin is None:
        return None
    if isinstance(origin, MessageOriginUser):
        return origin.sender_user.full_name
    if isinstance(origin, MessageOriginHiddenUser):
        return origin.sender_user_name
    if isinstance(origin, MessageOriginChat):
        return origin.sender_chat.title or origin.author_signature or "?"
    if isinstance(origin, MessageOriginChannel):
        return origin.chat.title or origin.author_signature or "?"
    return "?"


def _reply_snippet(replied: Message, language: str) -> str:
    """A short description of the message someone replied to — its own text
    if it has one (collapsed to one line, truncated), otherwise a localized
    placeholder for the media type (we don't re-download/re-transcribe just
    to build a label)."""
    snippet = replied.text or replied.caption
    if snippet:
        snippet = " ".join(snippet.split())
        if len(snippet) > _REPLY_SNIPPET_MAX:
            snippet = snippet[:_REPLY_SNIPPET_MAX] + "…"
        return snippet
    if replied.voice:
        return t(language, "reply_placeholder_voice")
    if replied.photo:
        return t(language, "reply_placeholder_photo")
    return t(language, "reply_placeholder_generic")


async def _extract_text(message: Message, stt: SpeechKit) -> str | None:
    """One incoming Telegram message -> the text the agent should see (voice
    transcribed, photos saved and referenced by path). None = nothing usable;
    the owner has already been told why."""
    text = message.text or message.caption
    # Incoming voice note: transcribe via SpeechKit and hand the agent the
    # text, marked as spoken (so she knows the medium; STT can garble words).
    if message.voice:
        transcript = await _transcribe_voice(message, stt)
        if transcript is None:
            return None  # already answered why
        text = t(settings.kaya_language, "voice_transcript_label", transcript=transcript)
    # Incoming photo: save it and tell the agent WHERE it is — the CLI's
    # Read tool views the file before answering.
    if message.photo:
        try:
            path = await _save_incoming_photo(message)
        except Exception:
            logger.exception("Failed to download an incoming photo")
            await message.answer(t(settings.kaya_language, "photo_download_failed"))
            return None
        note = t(settings.kaya_language, "photo_note", path=path)
        text = f"{text}\n{note}" if text else note
    if not text:
        await message.answer(t(settings.kaya_language, "unsupported_message"))
        return None
    # Reply/forward context is provenance about the WHOLE message, so it goes
    # in front of whatever content was assembled above (text/voice/photo).
    if message.reply_to_message:
        reply_note = t(
            settings.kaya_language,
            "reply_note",
            snippet=_reply_snippet(message.reply_to_message, settings.kaya_language),
        )
        text = f"{reply_note}\n{text}"
    sender = _forward_sender_label(message)
    if sender:
        forward_note = t(settings.kaya_language, "forward_note", sender=sender)
        text = f"{forward_note}\n{text}"
    return text


async def send_agent_text(bot, chat_id: int, reply: str) -> None:
    """Deliver one agent reply to a chat: direct image URLs become real Telegram
    photos; the rest is text. HTML parse_mode can choke on raw '<'/'&' from the
    model, so fall back to plain text rather than silently dropping.

    Takes (bot, chat_id) rather than a Message because it serves BOTH entry
    points: a reply to an incoming message, and a wake-up turn the agent
    started itself (where no Message exists)."""
    clean, images = _split_images(_strip_markdown(reply))
    if clean:
        try:
            await bot.send_message(chat_id, clean)
        except TelegramBadRequest:
            logger.warning("HTML send rejected; retrying as plain text")
            await bot.send_message(chat_id, clean, parse_mode=None)
    for url in images:
        try:
            await bot.send_photo(chat_id, url)
        except TelegramBadRequest:
            # Telegram couldn't fetch/parse that URL as a photo — give the
            # owner the link instead of losing it.
            await bot.send_message(chat_id, url)


async def _send_reply(message: Message, reply: str) -> None:
    """Deliver one agent reply in response to an incoming message."""
    await send_agent_text(message.bot, message.chat.id, reply)


class ChatTurns:
    """Tracks which chats have an agent turn in flight.

    WHY it exists outside build_router's closure: turns now start from TWO
    places — an incoming Telegram message, and a fired self-reminder that wakes
    the agent (agents/kaya/delivery.py). Both must never run at once for the
    same chat, or they race on the history table and the owner gets two
    unrelated replies interleaved. Single-threaded event loop: there is no
    await between the check and the set, so claim() needs no lock."""

    def __init__(self) -> None:
        self._busy: set[int] = set()

    def busy(self, chat_id: int) -> bool:
        return chat_id in self._busy

    def claim(self, chat_id: int) -> bool:
        """Take the chat if it is free. True = caller now owns the turn."""
        if chat_id in self._busy:
            return False
        self._busy.add(chat_id)
        return True

    def release(self, chat_id: int) -> None:
        self._busy.discard(chat_id)


def build_router(
    agent: Agent, allowed_ids: list[int], stt: SpeechKit, turns: ChatTurns | None = None
) -> Router:
    """Build Кая's aiogram router: owner gate + coalescing catch-all handler.

    `turns` is shared with the delivery receiver so a self-reminder wake-up
    can't run concurrently with a live conversation (see ChatTurns)."""
    router = Router()
    router.message.middleware(OwnerOnlyMiddleware(allowed_ids))

    # Per-chat message queue; the turn flag lives in ChatTurns because the
    # delivery receiver shares it.
    pending: dict[int, list[tuple[Message, str]]] = {}
    busy = turns if turns is not None else ChatTurns()
    # Which chats already have a MESSAGE worker draining them. Separate from
    # `busy` on purpose: `busy` can also be held by a wake-up turn (delivery
    # receiver), and "someone else is mid-turn" must NOT be read as "a worker
    # will pick my message up" — that would leave the queued message sitting
    # there until the owner happened to send another one.
    draining: set[int] = set()

    async def run_turn(chat_id: int, batch: list[tuple[Message, str]]) -> None:
        """One agent turn over a merged batch of owner messages."""
        # Several queued messages enter the model as ONE user turn, in order —
        # the owner "finished the thought", the agent answers the whole thought.
        merged = "\n\n".join(text for _, text in batch)
        last_message = batch[-1][0]
        started = time.monotonic()
        status_sent = False

        async def on_status(tool: str, detail: str) -> None:
            nonlocal status_sent
            # Keep the typing indicator alive (Telegram drops it after ~5s).
            await last_message.bot.send_chat_action(chat_id, "typing")
            if status_sent or time.monotonic() - started < _STATUS_AFTER_S:
                return
            line = _status_line(tool, detail)
            if line:
                status_sent = True
                await last_message.answer(line)

        await last_message.bot.send_chat_action(chat_id, "typing")
        try:
            reply = await agent.reply(merged, on_status=on_status)
        except Exception:
            logger.exception("Кая failed to handle a message")
            await last_message.answer(t(settings.kaya_language, "turn_crashed"))
            return
        await _send_reply(last_message, reply)

    @router.message()
    async def on_message(message: Message) -> None:
        text = await _extract_text(message, stt)
        if text is None:
            return
        chat_id = message.chat.id
        pending.setdefault(chat_id, []).append((message, text))
        if chat_id in draining:
            # A worker is already draining this chat: it picks this message up
            # in its next batch. Single-threaded event loop, no await between
            # the check and the add below — no lock needed.
            return
        draining.add(chat_id)
        try:
            # Drain until quiet: anything that arrived DURING a turn becomes
            # the next merged batch instead of a racing parallel turn.
            while pending[chat_id]:
                # Debounce a burst: keep waiting while messages keep landing.
                while True:
                    seen = len(pending[chat_id])
                    await asyncio.sleep(_DEBOUNCE_S)
                    if len(pending[chat_id]) == seen:
                        break
                # Take the chat. A wake-up turn may be holding it; wait it out
                # (anything arriving meanwhile just joins this batch) — the
                # owner's message must never be silently parked.
                while not busy.claim(chat_id):
                    await asyncio.sleep(_TURN_WAIT_S)
                batch = pending[chat_id]
                pending[chat_id] = []
                try:
                    await run_turn(chat_id, batch)
                except Exception:
                    # run_turn already handles a failed AGENT turn; what can
                    # still escape is the SEND (e.g. a reply over Telegram's
                    # 4096-char limit raises TelegramBadRequest on both the
                    # HTML and the plain-text attempt). That must not abort the
                    # drain loop: anything queued during this turn would then
                    # sit in `pending` with no worker, silently unanswered
                    # until the owner happened to write again — the same
                    # parked-message failure the queue exists to prevent.
                    logger.exception("Кая failed to deliver a turn's reply")
                finally:
                    busy.release(chat_id)
        finally:
            draining.discard(chat_id)

    return router
