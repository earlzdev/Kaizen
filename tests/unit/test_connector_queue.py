# =============================================================================
# Unit tests — agents/kaya/connector.py (per-chat turn serialization)
# =============================================================================
# WHAT: the coalescing queue and its interaction with ChatTurns, the flag now
#       SHARED with the delivery receiver (a fired self-reminder wakes the
#       agent and takes the same chat).
# WHY: two entry points fight over one chat. The failure modes are silent by
#       nature — a dropped owner message or two concurrent turns racing on the
#       history table both look like "Кая just didn't answer".
# HOW: the router's catch-all handler is called directly with duck-typed
#       aiogram objects; the debounce is shortened so the test is instant.
# =============================================================================

import asyncio
from types import SimpleNamespace

import pytest

from agents.kaya import connector
from agents.kaya.connector import ChatTurns, build_router

CHAT_ID = 42


@pytest.fixture(autouse=True)
def fast_timers(monkeypatch):
    """The real debounce is 4.5s — irrelevant to this logic, fatal to a suite."""
    monkeypatch.setattr(connector, "_DEBOUNCE_S", 0.01)
    monkeypatch.setattr(connector, "_TURN_WAIT_S", 0.01)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_chat_action(self, chat_id, action):
        pass

    async def send_message(self, chat_id, text, parse_mode=...):
        self.sent.append(text)


class FakeMessage:
    """Only what _extract_text / run_turn touch."""

    def __init__(self, text, bot):
        self.text = text
        self.caption = None
        self.voice = None
        self.photo = None
        self.chat = SimpleNamespace(id=CHAT_ID)
        self.bot = bot
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)


class FakeAgent:
    """Records every turn; optionally blocks inside reply() until released."""

    def __init__(self, gate: asyncio.Event | None = None):
        self.calls = []
        self._gate = gate

    async def reply(self, text, on_status=None):
        self.calls.append(text)
        if self._gate is not None:
            await self._gate.wait()
        return "ответ"


def _handler(agent, turns):
    router = build_router(agent, [1], stt=None, turns=turns)
    return router.message.handlers[0].callback


async def test_owner_message_waits_out_a_wake_instead_of_being_dropped():
    """A self-reminder wake holds the chat when the owner writes. The message
    must be answered as soon as the wake finishes — NOT parked until the owner
    happens to send another one."""
    bot, agent, turns = FakeBot(), FakeAgent(), ChatTurns()
    turns.claim(CHAT_ID)  # the wake-up turn owns the chat right now
    task = asyncio.create_task(_handler(agent, turns)(FakeMessage("привет", bot)))

    await asyncio.sleep(0.1)
    assert agent.calls == []  # still waiting, not racing the wake

    turns.release(CHAT_ID)
    await asyncio.wait_for(task, timeout=2)
    assert agent.calls == ["привет"]
    assert bot.sent == ["ответ"]
    assert not turns.busy(CHAT_ID)  # released for the next wake


async def test_a_live_turn_holds_the_chat_so_a_wake_is_refused():
    gate = asyncio.Event()
    bot, agent, turns = FakeBot(), FakeAgent(gate), ChatTurns()
    task = asyncio.create_task(_handler(agent, turns)(FakeMessage("привет", bot)))
    await asyncio.sleep(0.1)

    assert turns.busy(CHAT_ID)          # a wake arriving now gets its 503
    assert not turns.claim(CHAT_ID)
    gate.set()
    await asyncio.wait_for(task, timeout=2)
    assert not turns.busy(CHAT_ID)


async def test_messages_arriving_during_a_turn_merge_into_the_next_batch():
    gate = asyncio.Event()
    bot, agent, turns = FakeBot(), FakeAgent(gate), ChatTurns()
    handler = _handler(agent, turns)
    first = asyncio.create_task(handler(FakeMessage("раз", bot)))
    await asyncio.sleep(0.1)  # the first turn is now in flight (blocked)

    await handler(FakeMessage("два", bot))    # queued, returns immediately
    await handler(FakeMessage("три", bot))
    gate.set()
    await asyncio.wait_for(first, timeout=2)
    # One turn per batch, the burst merged — never three parallel turns.
    assert agent.calls == ["раз", "два\n\nтри"]


async def test_a_failed_send_does_not_orphan_the_queued_messages():
    """A reply that Telegram refuses (too long, flood limit) raises out of
    run_turn. The drain loop must survive it — otherwise whatever the owner
    sent DURING that turn stays in `pending` with no worker and is silently
    never answered."""

    class ExplodingBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.fail_next = True

        async def send_message(self, chat_id, text, parse_mode=...):
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("message is too long")
            await super().send_message(chat_id, text, parse_mode)

    gate = asyncio.Event()
    bot, agent, turns = ExplodingBot(), FakeAgent(gate), ChatTurns()
    handler = _handler(agent, turns)
    first = asyncio.create_task(handler(FakeMessage("раз", bot)))
    await asyncio.sleep(0.1)          # first turn in flight (blocked)
    await handler(FakeMessage("два", bot))   # queued behind the failing send
    gate.set()
    await asyncio.wait_for(first, timeout=2)

    assert agent.calls == ["раз", "два"]  # the queued message still ran
    assert bot.sent == ["ответ"]          # only the second reply got through
    assert not turns.busy(CHAT_ID)
