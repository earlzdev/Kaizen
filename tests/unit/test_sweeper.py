# =============================================================================
# Unit tests — brain/sweeper.py (sweep resilience, backoff, delivery routing)
# =============================================================================
# WHAT: sweep_once over fake stores/delivery: marking semantics, batch
#       isolation, the Step-9 per-reminder exponential backoff (via an
#       injectable clock — no sleeping), and the explicit-fallback routing that
#       replaced "any agent with an address".
# WHY: with Кая down, every sweep used to hammer a push per reminder per 30s;
#       and a second registered agent would have started receiving HER
#       reminders. Both behaviors are pinned here.
# HOW: ReminderSweeper takes its collaborators + clock via the constructor —
#       plain duck-typed fakes, no DB, no HTTP.
# =============================================================================

from types import SimpleNamespace

from brain.sweeper import ReminderSweeper


def reminder(rid, agent_id=1, text="пей воду", audience="owner"):
    return SimpleNamespace(id=rid, agent_id=agent_id, text=text, audience=audience)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeMemory:
    def __init__(self, due):
        self.due = due
        self.marked = []

    async def due_reminders(self, now):
        return self.due

    async def mark_reminder_delivered(self, reminder_id):
        self.marked.append(reminder_id)
        self.due = [r for r in self.due if r.id != reminder_id]


class FakeAgents:
    """get_by_id serves the OWNING agent's addr; get_by_slug the fallback map."""

    def __init__(self, owner_addr=None, by_slug=None):
        self._owner_addr = owner_addr
        self._by_slug = by_slug or {}

    async def get_by_id(self, agent_id):
        return SimpleNamespace(delivery_addr=self._owner_addr)

    async def get_by_slug(self, slug):
        addr = self._by_slug.get(slug)
        return SimpleNamespace(delivery_addr=addr) if addr is not None else None


class FakeDelivery:
    """push() outcomes scripted per call: True/False deliver, Exception raises.
    The last outcome repeats once the script runs out."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.pushes = []

    async def push(self, addr, event):
        self.pushes.append((addr, event))
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


OWNER_ADDR = "http://kaya:8780/deliver"


def sweeper(memory, agents, delivery, *, default_slug="", clock=None):
    return ReminderSweeper(
        memory, agents, delivery, interval_seconds=30,
        default_delivery_slug=default_slug, clock=clock or FakeClock(),
    )


async def test_successful_push_marks_delivered():
    memory = FakeMemory([reminder(1)])
    delivery = FakeDelivery([True])
    delivered = await sweeper(memory, FakeAgents(owner_addr=OWNER_ADDR), delivery).sweep_once()
    assert delivered == 1
    assert memory.marked == [1]
    # Typed event, RAW text (Step 7): presentation belongs to the agent.
    assert delivery.pushes[0][1] == {"kind": "reminder", "text": "пей воду"}


async def test_self_reminder_is_pushed_as_an_agent_wake():
    """audience="agent" must NOT be relayed to the owner as text — it wakes
    the agent instead, and the receiver decides what (if anything) to say."""
    memory = FakeMemory([reminder(1, text="спроси, как долетел", audience="agent")])
    delivery = FakeDelivery([True])
    await sweeper(memory, FakeAgents(owner_addr=OWNER_ADDR), delivery).sweep_once()
    assert delivery.pushes[0][1] == {
        "kind": "agent_wake", "text": "спроси, как долетел"
    }


async def test_self_note_is_never_handed_to_another_agent():
    """An owner reminder may fall back to the default agent — it's just text.
    A self-note must NOT: agent B would run a full turn on a plan agent A made
    and message the owner from it."""
    memory = FakeMemory([reminder(1, text="спроси, как долетел", audience="agent")])
    delivery = FakeDelivery([True])
    agents = FakeAgents(owner_addr=None, by_slug={"kaya": OWNER_ADDR})
    s = sweeper(memory, agents, delivery, default_slug="kaya")
    assert await s.sweep_once() == 0
    assert delivery.pushes == []
    assert memory.marked == []   # stays due until its author is reachable


async def test_failed_push_leaves_reminder_due():
    memory = FakeMemory([reminder(1)])
    s = sweeper(memory, FakeAgents(owner_addr=OWNER_ADDR), FakeDelivery([False]))
    assert await s.sweep_once() == 0
    assert memory.marked == []  # stays due — retried after the backoff


async def test_one_exploding_reminder_does_not_abort_the_batch():
    memory = FakeMemory([reminder(1), reminder(2), reminder(3)])
    delivery = FakeDelivery([RuntimeError("boom"), True, True])
    delivered = await sweeper(memory, FakeAgents(owner_addr=OWNER_ADDR), delivery).sweep_once()
    assert delivered == 2
    assert memory.marked == [2, 3]
    assert len(delivery.pushes) == 3


# ----- Step 9: per-reminder exponential backoff ----------------------------


async def test_failed_push_backs_off_then_retries_after_delay():
    clock = FakeClock()
    memory = FakeMemory([reminder(1)])
    delivery = FakeDelivery([False])
    s = sweeper(memory, FakeAgents(owner_addr=OWNER_ADDR), delivery, clock=clock)

    await s.sweep_once()
    assert len(delivery.pushes) == 1
    # Immediately after: still backing off (first delay = 30 * 2^1 = 60s).
    await s.sweep_once()
    assert len(delivery.pushes) == 1  # no hammering
    # After the delay has passed, the push is attempted again.
    clock.now += 61
    await s.sweep_once()
    assert len(delivery.pushes) == 2


async def test_backoff_doubles_and_caps():
    clock = FakeClock()
    memory = FakeMemory([reminder(1)])
    s = sweeper(memory, FakeAgents(owner_addr=OWNER_ADDR), FakeDelivery([False]), clock=clock)
    # Fail many times, always advancing past whatever delay was set.
    for _ in range(12):
        await s.sweep_once()
        clock.now += 100_000
    failures, retry_at = s._backoff[1]
    assert failures == 12
    # The last recorded delay is capped at 30 minutes.
    assert retry_at - (clock.now - 100_000) <= 1800.0 + 1e-6


async def test_success_clears_backoff_state():
    clock = FakeClock()
    memory = FakeMemory([reminder(1)])
    delivery = FakeDelivery([False, True])
    s = sweeper(memory, FakeAgents(owner_addr=OWNER_ADDR), delivery, clock=clock)
    await s.sweep_once()
    clock.now += 61
    await s.sweep_once()
    assert memory.marked == [1]
    assert s._backoff == {}


async def test_backoff_state_pruned_when_reminder_leaves_due_set():
    clock = FakeClock()
    memory = FakeMemory([reminder(1)])
    s = sweeper(memory, FakeAgents(owner_addr=OWNER_ADDR), FakeDelivery([False]), clock=clock)
    await s.sweep_once()
    assert 1 in s._backoff
    memory.due = []  # cancelled via the admin panel, say
    await s.sweep_once()
    assert s._backoff == {}


# ----- Step 9: explicit delivery fallback ----------------------------------


async def test_no_owner_addr_and_no_default_skips():
    memory = FakeMemory([reminder(1)])
    delivery = FakeDelivery([True])
    s = sweeper(memory, FakeAgents(owner_addr=None, by_slug={"kuzya": "http://kuzya:1/d"}), delivery)
    assert await s.sweep_once() == 0
    # The OTHER agent's address must NOT be used (the old arbitrary fallback).
    assert delivery.pushes == []
    assert memory.marked == []


async def test_fallback_goes_only_to_the_configured_default():
    memory = FakeMemory([reminder(1)])
    delivery = FakeDelivery([True])
    agents = FakeAgents(owner_addr=None, by_slug={"kaya": OWNER_ADDR, "kuzya": "http://kuzya:1/d"})
    s = sweeper(memory, agents, delivery, default_slug="kaya")
    assert await s.sweep_once() == 1
    assert delivery.pushes[0][0] == OWNER_ADDR


async def test_owning_agents_addr_wins_over_default():
    memory = FakeMemory([reminder(1)])
    delivery = FakeDelivery([True])
    agents = FakeAgents(owner_addr="http://owner:1/d", by_slug={"kaya": OWNER_ADDR})
    s = sweeper(memory, agents, delivery, default_slug="kaya")
    await s.sweep_once()
    assert delivery.pushes[0][0] == "http://owner:1/d"
