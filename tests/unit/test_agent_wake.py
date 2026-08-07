# =============================================================================
# Unit tests — Agent.wake (a fired self-reminder starts a turn)
# =============================================================================
# WHAT: the wake turn's contract — the model sees the full instructions, local
#       history keeps only a short marker, recall runs on the NOTE, the agent
#       may choose silence, and a broken turn never messages the owner.
# WHY: this is the one path where Кая speaks WITHOUT being spoken to, so its
#       failure modes are asymmetric: a wrong message out of nowhere is worse
#       than no message.
# HOW: the same fakes as the gate tests — a scripted Runner and a Brain stub.
# =============================================================================

from agents.core.agent import Agent
from agents.core.history import InMemoryHistory
from agents.core.prompts import AGENT_WAKE_SKIP, EMPTY_RECALL_MARKER
from agents.core.runner import RunResult

NOTE = "Владелец прилетел в Тбилиси — спроси, как долетел"


class FakeBrain:
    def __init__(self):
        self.tool_calls = []
        self.recall_queries = []

    async def usage_notes(self):
        return []

    async def recall(self, query):
        self.recall_queries.append(query)
        return EMPTY_RECALL_MARKER

    async def call_tool(self, name, arguments):
        self.tool_calls.append((name, arguments))
        if name == "get_profile":
            return "No profile is set yet.", False
        return "ok", False


class FakeRunner:
    def __init__(self, results):
        self._results = list(results)
        self.runs = []
        self.kwargs = []  # (runtime, resume) per call

    async def run(self, system, messages, on_status=None, *, runtime="", resume=None):
        self.runs.append((system, [dict(m) for m in messages]))
        self.kwargs.append((runtime, resume))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _agent(runner, brain=None, history=None):
    return Agent(
        soul="SOUL",
        brain=brain or FakeBrain(),
        history=history or InMemoryHistory(),
        runner=runner,
    )


async def test_wake_returns_the_agents_message():
    runner = FakeRunner([RunResult("Ну как долетел?")])
    assert await _agent(runner).wake(NOTE) == "Ну как долетел?"


async def test_model_sees_the_instructions_history_keeps_a_short_marker():
    history = InMemoryHistory()
    runner = FakeRunner([RunResult("как долетел?")])
    await _agent(runner, history=history).wake(NOTE)

    # THIS turn: the last user message carries the full wake instructions.
    prompt = runner.runs[0][1][-1]
    assert prompt["role"] == "user"
    assert "Automatic wake-up" in prompt["content"]
    assert NOTE in prompt["content"]

    # Persisted history: only the compact marker + the reply — no scaffolding,
    # so the owner's next message doesn't read as an answer to harness text.
    stored = await history.load()
    assert "Automatic wake-up" not in stored[0]["content"]
    assert NOTE in stored[0]["content"]
    assert stored[-1] == {"role": "assistant", "content": "как долетел?"}


async def test_recall_runs_on_the_note_not_the_template():
    """The note is the subject of the turn; searching long-term memory by it is
    how a wake-up recovers context that scrolled out of local history."""
    brain = FakeBrain()
    runner = FakeRunner([RunResult("привет")])
    await _agent(runner, brain).wake(NOTE)
    assert brain.recall_queries == [NOTE]


async def test_wake_prompt_points_at_the_deeper_memory_tools():
    """A note whose conversation left the 30-message window must still produce
    a grounded message — the prompt has to say where to look."""
    runner = FakeRunner([RunResult("hi")])
    await _agent(runner).wake(NOTE)
    prompt = runner.runs[0][1][-1]["content"]
    assert "recall_memory" in prompt and "search_conversations" in prompt


async def test_wake_carries_the_recent_conversation():
    """The flight chat itself, when it IS still in the window, arrives as
    ordinary history — the wake turn sees it without any special plumbing."""
    history = InMemoryHistory()
    await history.append("user", "лечу в Тбилиси в 14:20, рейс 3 часа")
    await history.append("assistant", "хорошего полёта")
    runner = FakeRunner([RunResult("как долетел?")])
    await _agent(runner, history=history).wake(NOTE)

    delivered = [m["content"] for m in runner.runs[0][1]]
    assert "лечу в Тбилиси в 14:20, рейс 3 часа" in delivered


async def test_agent_can_choose_silence():
    history = InMemoryHistory()
    runner = FakeRunner([RunResult(AGENT_WAKE_SKIP)])
    assert await _agent(runner, history=history).wake(NOTE) is None
    # The marker stays (the wake DID happen), but nothing is sent or stored
    # as an assistant turn.
    stored = await history.load()
    assert len(stored) == 1 and stored[0]["role"] == "user"


async def test_silence_survives_a_research_wake():
    """A wake that searched a lot ends in the self-check gate — the skip marker
    must be honoured BEFORE that, or the verifier can rewrite «say nothing»
    into a real message to the owner."""
    runner = FakeRunner([RunResult(AGENT_WAKE_SKIP, tools_used=["find_online"] * 4)])
    assert await _agent(runner).wake(NOTE) is None
    assert len(runner.runs) == 1  # no gate turn was run at all


async def test_failed_turn_stays_silent_rather_than_apologising():
    runner = FakeRunner([RunResult("Error: backend failed")])
    assert await _agent(runner).wake(NOTE) is None


async def test_wake_is_archived_when_it_speaks():
    brain = FakeBrain()
    runner = FakeRunner([RunResult("как долетел?")])
    await _agent(runner, brain).wake(NOTE)
    archived = [a for n, a in brain.tool_calls if n == "log_conversation"]
    assert archived and archived[0]["agent_reply"] == "как долетел?"


async def test_wake_turn_gets_the_same_system_prompt_shape():
    runner = FakeRunner([RunResult("hi")])
    await _agent(runner).wake(NOTE)
    system = runner.runs[0][0]
    runtime, _ = runner.kwargs[0]
    assert system.startswith("SOUL")            # persona head
    assert "## Runtime context" in runtime      # clock/profile/recall as usual
    assert "## Runtime context" not in system   # ...and NOT in the cached head
