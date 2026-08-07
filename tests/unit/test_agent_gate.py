# =============================================================================
# Unit tests — agents/core/agent.py (the self-check gate decision table)
# =============================================================================
# WHAT: Agent.reply end to end with a fake Runner and a fake Brain client —
#       which turns get the fact-check / style gate, and how the verdict
#       markers (VERIFIED-OK / FINAL:) are parsed.
# WHY: this decision table (agent.py _final_gate) is dense, pure logic that
#       decides what the owner actually receives; it had zero coverage.
# HOW: FakeRunner returns scripted RunResults; FakeBrain satisfies the parts of
#       BrainMCPClient the Agent touches (recall, call_tool).
# =============================================================================

from agents.core.agent import Agent
from agents.core.history import InMemoryHistory
from agents.core.prompts import EMPTY_RECALL_MARKER, FINAL_MARKER, VERIFY_OK_MARKER
from agents.core.runner import RunResult

LONG = "x" * 400  # ≥ STYLE_GATE_MIN_CHARS (300)
SHORT = "ok"


class FakeBrain:
    """The slice of BrainMCPClient the Agent uses (recall + call_tool +
    usage_notes)."""

    def __init__(self, usage_notes=()):
        self.tool_calls = []
        self._usage_notes = list(usage_notes)

    async def usage_notes(self):
        return self._usage_notes

    async def recall(self, query):
        return EMPTY_RECALL_MARKER

    async def call_tool(self, name, arguments):
        self.tool_calls.append((name, arguments))
        if name == "get_profile":
            return "No profile is set yet.", False
        return "ok", False


class FakeRunner:
    """Runner returning scripted results; records every run() request."""

    def __init__(self, results):
        self._results = list(results)
        self.runs = []  # (system, messages) per call
        self.kwargs = []  # (runtime, resume) per call

    async def run(self, system, messages, on_status=None, *, runtime="", resume=None):
        self.runs.append((system, [dict(m) for m in messages]))
        self.kwargs.append((runtime, resume))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _agent(runner, brain=None, gate_runner=None):
    return Agent(
        soul="SOUL", brain=brain or FakeBrain(), history=InMemoryHistory(),
        runner=runner, gate_runner=gate_runner,
    )


class StatusRecorder:
    """Collects on_status(tool, detail) calls the way a connector would."""

    def __init__(self):
        self.calls = []

    async def __call__(self, tool, detail):
        self.calls.append((tool, detail))

    @property
    def tools(self):
        return [tool for tool, _ in self.calls]


async def test_short_no_search_reply_skips_the_gate():
    runner = FakeRunner([RunResult(SHORT)])
    assert await _agent(runner).reply("hi") == SHORT
    assert len(runner.runs) == 1  # no second (gate) turn


async def test_quick_lookup_short_answer_skips_fact_check():
    # ≤3 search ops AND a short answer = the quick-lookup exemption.
    runner = FakeRunner([RunResult(SHORT, tools_used=["find_online"])])
    assert await _agent(runner).reply("погода?") == SHORT
    assert len(runner.runs) == 1


async def test_deep_search_gets_fact_check_and_ok_keeps_draft():
    searched = ["find_online", "read_page", "read_page", "read_page"]  # > 3 ops
    runner = FakeRunner(
        [RunResult(SHORT, tools_used=searched), RunResult(VERIFY_OK_MARKER)]
    )
    assert await _agent(runner).reply("q") == SHORT
    assert len(runner.runs) == 2
    # The gate turn must carry the draft + the check request on top of history.
    gate_messages = runner.runs[1][1]
    assert gate_messages[-2] == {"role": "assistant", "content": SHORT}
    assert gate_messages[-1]["role"] == "user"


async def test_long_answer_without_search_gets_style_gate():
    runner = FakeRunner([RunResult(LONG), RunResult(VERIFY_OK_MARKER)])
    assert await _agent(runner).reply("q") == LONG
    assert len(runner.runs) == 2


async def test_final_marker_replaces_draft_with_rewrite():
    runner = FakeRunner(
        [RunResult(LONG), RunResult(f"чиню пару фраз\n{FINAL_MARKER}\nCLEANED")]
    )
    assert await _agent(runner).reply("q") == "CLEANED"


async def test_rewrite_without_marker_keeps_the_draft():
    runner = FakeRunner([RunResult(LONG), RunResult("a rewrite with no marker")])
    assert await _agent(runner).reply("q") == LONG


async def test_gate_failure_never_eats_the_answer():
    runner = FakeRunner([RunResult(LONG), RuntimeError("gate died")])
    assert await _agent(runner).reply("q") == LONG


async def test_error_result_skips_gate_and_archive():
    brain = FakeBrain()
    runner = FakeRunner([RunResult("Error: backend failed")])
    assert await _agent(runner, brain).reply("q") == "Error: backend failed"
    assert len(runner.runs) == 1
    # A failed turn is not archived (log_conversation must not be called).
    assert all(name != "log_conversation" for name, _ in brain.tool_calls)


async def test_good_turn_is_archived_and_persisted():
    brain = FakeBrain()
    runner = FakeRunner([RunResult(SHORT)])
    agent = _agent(runner, brain)
    await agent.reply("привет")
    assert ("log_conversation", {"owner_message": "привет", "agent_reply": SHORT}) in [
        (n, a) for n, a in brain.tool_calls
    ]
    # History got both turns (user + assistant).
    assert await agent._history.load() == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": SHORT},
    ]


# ----- the "🧐 Перепроверяю факты…" status line (owner's ask, 2026-07-29) ----


async def test_style_gate_runs_silently():
    """A long answer with NO search still gets the style gate — but the owner
    must not be told facts are being re-checked (there are none to check)."""
    runner = FakeRunner([RunResult(LONG), RunResult(VERIFY_OK_MARKER)])
    status = StatusRecorder()
    assert await _agent(runner).reply("q", on_status=status) == LONG
    assert len(runner.runs) == 2          # the gate DID run
    assert "self_check" not in status.tools  # ...silently


async def test_single_search_op_gate_runs_silently():
    """One lookup + a long answer: fact-checked, but too shallow to announce."""
    runner = FakeRunner(
        [RunResult(LONG, tools_used=["find_online"]), RunResult(VERIFY_OK_MARKER)]
    )
    status = StatusRecorder()
    await _agent(runner).reply("q", on_status=status)
    assert len(runner.runs) == 2
    assert "self_check" not in status.tools


async def test_real_research_announces_the_fact_check():
    searched = ["find_online", "read_page", "read_page"]
    runner = FakeRunner(
        [RunResult(LONG, tools_used=searched), RunResult(VERIFY_OK_MARKER)]
    )
    status = StatusRecorder()
    await _agent(runner).reply("q", on_status=status)
    assert status.tools == ["self_check"]


async def test_status_callback_failure_never_breaks_the_turn():
    async def broken_status(tool, detail):
        raise RuntimeError("connector died")

    searched = ["find_online", "read_page"]
    runner = FakeRunner(
        [RunResult(LONG, tools_used=searched), RunResult(VERIFY_OK_MARKER)]
    )
    assert await _agent(runner).reply("q", on_status=broken_status) == LONG


async def test_system_prompt_is_soul_headed():
    runner = FakeRunner([RunResult(SHORT)])
    await _agent(runner).reply("hi")
    system = runner.runs[0][0]
    assert system.startswith("SOUL")  # stable head first — prompt-cache contract


async def test_tool_usage_block_is_part_of_the_stable_head():
    runner = FakeRunner([RunResult(SHORT)])
    brain = FakeBrain(usage_notes=[("add_reminder", "pass an offset")])
    await _agent(runner, brain).reply("hi", on_status=None)
    system = runner.runs[0][0]
    assert "## How to use your tools" in system
    assert "pass an offset" in system


async def test_runtime_context_stays_out_of_the_cacheable_head():
    """The prompt-cache contract: the per-message block (clock, profile,
    recall) must reach the runner as the separate `runtime` argument. Merged
    into `system` it would change the head every turn and void the cache."""
    runner = FakeRunner([RunResult(SHORT)])
    await _agent(runner).reply("hi")
    system, _ = runner.runs[0]
    runtime, resume = runner.kwargs[0]
    assert "## Runtime context" not in system
    assert "Current date and time" not in system
    assert runtime.startswith("## Runtime context")
    assert "Current date and time" in runtime
    assert resume is None  # nothing to resume on the first turn


async def test_gate_resumes_the_drafts_session():
    """The gate must continue the draft's session rather than replay it — that
    is what stops a gated turn costing two full transcripts + two system
    prompts on a backend that keeps sessions."""
    runner = FakeRunner(
        [RunResult(LONG, session_id="sess-1"), RunResult(VERIFY_OK_MARKER)]
    )
    assert await _agent(runner).reply("hi") == LONG
    assert runner.kwargs[0][1] is None       # the draft turn starts fresh
    assert runner.kwargs[1][1] == "sess-1"   # the gate resumes into it


async def test_gate_without_a_session_id_replays():
    """A backend that keeps no session (the API tool-loop) reports session_id
    None; the gate must then fall back to the full transcript."""
    runner = FakeRunner([RunResult(LONG), RunResult(VERIFY_OK_MARKER)])
    await _agent(runner).reply("hi")
    assert runner.kwargs[1][1] is None
    # ...and the replayed messages carry the draft + the check request.
    gate_messages = runner.runs[1][1]
    assert gate_messages[-2]["content"] == LONG


async def test_separate_gate_runner_is_used_and_never_resumes():
    """A configured gate runner is a different model and session, so there is
    nothing of ours to resume into — it must get the full transcript."""
    main = FakeRunner([RunResult(LONG, session_id="sess-1")])
    gate = FakeRunner([RunResult(VERIFY_OK_MARKER)])
    assert await _agent(main, gate_runner=gate).reply("hi") == LONG
    assert len(main.runs) == 1 and len(gate.runs) == 1
    assert gate.kwargs[0][1] is None
    assert gate.runs[0][1][-2]["content"] == LONG


async def test_no_usage_notes_means_no_block():
    runner = FakeRunner([RunResult(SHORT)])
    await _agent(runner, FakeBrain()).reply("hi")
    assert "## How to use your tools" not in runner.runs[0][0]


async def test_usage_notes_failure_never_blocks_a_reply():
    class BrokenBrain(FakeBrain):
        async def usage_notes(self):
            raise RuntimeError("brain down")

    runner = FakeRunner([RunResult(SHORT)])
    assert await _agent(runner, BrokenBrain()).reply("hi") == SHORT
    assert "## How to use your tools" not in runner.runs[0][0]
