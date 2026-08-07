# =============================================================================
# Unit tests — agents/kaya/delivery.py (the push receiver)
# =============================================================================
# WHAT: Кая's delivery endpoint over a real aiohttp TestServer with a fake Bot:
#       token auth, the typed DeliveryEvent contract, the reminder-prefix
#       framing (moved here from Brain's sweeper in Step 7), and the 502 path
#       that makes Brain retry.
# WHY: this is the only door Brain can push through — a regression here means
#       reminders silently stop arriving.
# HOW: the Bot is duck-typed (only send_message is used); no Telegram, no Brain.
#      A wake is answered 202 and runs in the background (Brain gives a push
#      10s, a turn takes longer), so the wake tests await `_drain` — the same
#      task set the app's shutdown waits on — before asserting.
# =============================================================================

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from agents.kaya.config import settings
from agents.kaya.connector import ChatTurns
from agents.kaya.delivery import WAKE_TASKS, build_delivery_app
from agents.kaya.strings import t

TOKEN = "delivery-tok"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeBot:
    def __init__(self, fail=False):
        self.sent = []
        self._fail = fail

    async def send_message(self, chat_id, text, parse_mode=...):
        if self._fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text))

    async def send_photo(self, chat_id, url):
        self.sent.append((chat_id, f"[photo] {url}"))


class FakeAgent:
    """Stands in for the Agent's wake() — records notes, returns a scripted
    reply (None = the agent chose silence, Exception = the turn blew up).
    `gate` (an Event) holds the turn open, standing in for the minutes a real
    one takes."""

    def __init__(self, reply="как долетел?", gate=None):
        self.notes = []
        self._reply = reply
        self._gate = gate

    async def wake(self, note, on_status=None):
        self.notes.append(note)
        if self._gate is not None:
            await self._gate.wait()
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


async def _drain(client) -> None:
    """Wait for the background wake turns, like the app's shutdown does."""
    tasks = list(client.app[WAKE_TASKS])
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _client(bot, token=TOKEN, agent=None, turns=None) -> TestClient:
    client = TestClient(
        TestServer(
            build_delivery_app(bot, owner_id=42, token=token, agent=agent, turns=turns)
        )
    )
    await client.start_server()
    return client


async def test_reminder_gets_kaya_framing():
    bot = FakeBot()
    client = await _client(bot)
    try:
        resp = await client.post(
            "/deliver", json={"kind": "reminder", "text": "пей воду"}, headers=AUTH
        )
        assert resp.status == 200
        # The prefix is added HERE (Step 7) — Brain sends raw text. Resolved
        # via strings.t(), not hardcoded, so this doesn't depend on the
        # machine's KAYA_LANGUAGE.
        prefix = t(settings.kaya_language, "reminder_prefix")
        assert bot.sent == [(42, f"{prefix}пей воду")]
    finally:
        await client.close()


async def test_bad_token_and_empty_token_reject():
    client = await _client(FakeBot())
    try:
        resp = await client.post(
            "/deliver", json={"kind": "reminder", "text": "x"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status == 401
    finally:
        await client.close()
    # Fail-safe: an EMPTY configured token rejects even a matching header.
    client = await _client(FakeBot(), token="")
    try:
        resp = await client.post(
            "/deliver", json={"kind": "reminder", "text": "x"},
            headers={"Authorization": "Bearer "},
        )
        assert resp.status == 401
    finally:
        await client.close()


async def test_malformed_event_is_400():
    bot = FakeBot()
    client = await _client(bot)
    try:
        for body in ({"kind": "nope", "text": "x"}, {"text": "x"}, {"kind": "reminder"}, [1]):
            resp = await client.post("/deliver", json=body, headers=AUTH)
            assert resp.status == 400
        assert bot.sent == []
    finally:
        await client.close()


# ----- agent_wake: a self-reminder fires and Кая runs a real turn -----------


async def _wake(client, text="Владелец прилетел — спроси, как долетел"):
    return await client.post(
        "/deliver", json={"kind": "agent_wake", "text": text}, headers=AUTH
    )


async def test_wake_runs_a_turn_and_sends_what_she_decides():
    bot, agent = FakeBot(), FakeAgent("ну как, долетел нормально?")
    client = await _client(bot, agent=agent)
    try:
        resp = await _wake(client)
        assert resp.status == 202
        await _drain(client)
    finally:
        await client.close()
    # The NOTE went to the agent; the OWNER got her words, not the note.
    assert agent.notes == ["Владелец прилетел — спроси, как долетел"]
    assert bot.sent == [(42, "ну как, долетел нормально?")]


async def test_push_is_answered_before_the_turn_finishes():
    """The whole point of 202: Brain's DeliveryClient times a push out after
    10s, so answering only when the turn is done meant every wake looked
    failed and got re-delivered — the owner messaged again each sweep."""
    gate = asyncio.Event()
    bot, agent = FakeBot(), FakeAgent("готово", gate=gate)
    client = await _client(bot, agent=agent)
    try:
        resp = await _wake(client)          # returns while the turn is stuck
        assert resp.status == 202
        assert bot.sent == []
        gate.set()
        await _drain(client)
        assert bot.sent == [(42, "готово")]
    finally:
        await client.close()


async def test_wake_with_no_message_sends_nothing():
    bot, agent = FakeBot(), FakeAgent(None)  # the agent chose silence
    client = await _client(bot, agent=agent)
    try:
        resp = await _wake(client)
        assert resp.status == 202
        await _drain(client)
    finally:
        await client.close()
    assert bot.sent == []


async def test_wake_is_refused_while_a_turn_is_running():
    """503 so Brain's per-reminder backoff brings it back after the chat is
    free — never two concurrent turns racing on the history table."""
    bot, agent, turns = FakeBot(), FakeAgent(), ChatTurns()
    turns.claim(42)  # a live conversation is mid-turn
    client = await _client(bot, agent=agent, turns=turns)
    try:
        assert (await _wake(client)).status == 503
    finally:
        await client.close()
    assert agent.notes == [] and bot.sent == []


async def test_wake_releases_the_chat_afterwards():
    bot, agent, turns = FakeBot(), FakeAgent(), ChatTurns()
    client = await _client(bot, agent=agent, turns=turns)
    try:
        await _wake(client)
        await _drain(client)
        assert not turns.busy(42)   # released, so the owner can talk again
        assert (await _wake(client)).status == 202
        await _drain(client)
    finally:
        await client.close()


async def test_wake_without_a_wired_agent_is_503_not_a_crash():
    bot = FakeBot()
    client = await _client(bot, agent=None)
    try:
        assert (await _wake(client)).status == 503
    finally:
        await client.close()


async def test_crashed_wake_turn_is_not_retried_forever():
    """A turn that raises would raise again next sweep — consume it instead of
    asking Brain to retry, and never message the owner about it."""
    bot, agent, turns = FakeBot(), FakeAgent(RuntimeError("boom")), ChatTurns()
    client = await _client(bot, agent=agent, turns=turns)
    try:
        resp = await _wake(client)
        assert resp.status == 202
        await _drain(client)
    finally:
        await client.close()
    assert bot.sent == []
    assert not turns.busy(42)  # released even on the exception path


async def test_telegram_failure_is_502_so_brain_retries():
    client = await _client(FakeBot(fail=True))
    try:
        resp = await client.post(
            "/deliver", json={"kind": "reminder", "text": "x"}, headers=AUTH
        )
        assert resp.status == 502
    finally:
        await client.close()


async def test_health():
    client = await _client(FakeBot())
    try:
        assert (await client.get("/health")).status == 200
    finally:
        await client.close()
