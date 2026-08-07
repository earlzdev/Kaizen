# =============================================================================
# Unit tests — agents/core/loop.py (the tool-use loop)
# =============================================================================
# WHAT: AgentLoop driven by a scripted fake LLMClient and a fake ToolSource —
#       exactly the seams the loop was designed around (see loop.py WHY notes).
# WHY: this is the core turn engine; its contracts (tool_result echo, error
#       mapping, iteration cap, input immutability) had zero coverage.
# HOW: no network, no SDK — fakes satisfy the Protocols structurally.
# =============================================================================

import pytest

from agents.core.llm import AssistantTurn, ToolCall
from agents.core.loop import _CAP_FALLBACK, AgentLoop


class FakeLLM:
    """LLMClient returning pre-scripted turns; records the messages it saw."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = []  # the `messages` list passed to each generate()

    async def generate(self, system, messages, tools, max_tokens=4096, *, runtime=""):
        self.calls.append([dict(m) for m in messages])
        return self._turns.pop(0)


class FakeTools:
    """ToolSource with one tool; can be told to raise or return an error."""

    def __init__(self, result=("ok", False), raise_exc=None):
        self._result = result
        self._raise = raise_exc
        self.calls = []

    async def list_tools(self):
        return [{"name": "t", "description": "", "input_schema": {"type": "object"}}]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self._raise is not None:
            raise self._raise
        return self._result


def _tool_turn(text=""):
    call = ToolCall(id="tu_1", name="t", input={"q": "x"})
    return AssistantTurn(text=text, tool_calls=[call], content_blocks=[{"fake": "block"}])


async def test_plain_text_returns_immediately():
    llm = FakeLLM([AssistantTurn(text="hi")])
    result = await AgentLoop(llm, FakeTools()).run("sys", [{"role": "user", "content": "u"}])
    assert result.text == "hi"
    assert result.tools_used == []


async def test_tool_call_roundtrip_feeds_result_back():
    llm = FakeLLM([_tool_turn(), AssistantTurn(text="done")])
    tools = FakeTools(result=("tool says", False))
    result = await AgentLoop(llm, tools).run("sys", [{"role": "user", "content": "u"}])
    assert result.text == "done"
    assert result.tools_used == ["t"]
    assert tools.calls == [("t", {"q": "x"})]
    # The second model call must see: assistant content echoed + a matching tool_result.
    second = llm.calls[1]
    assert second[-2]["role"] == "assistant"
    tool_result = second[-1]["content"][0]
    assert tool_result["tool_use_id"] == "tu_1"
    assert tool_result["content"] == "tool says"
    assert tool_result["is_error"] is False


async def test_tool_exception_becomes_error_result_not_crash():
    llm = FakeLLM([_tool_turn(), AssistantTurn(text="recovered")])
    tools = FakeTools(raise_exc=RuntimeError("boom"))
    result = await AgentLoop(llm, tools).run("sys", [{"role": "user", "content": "u"}])
    assert result.text == "recovered"
    tool_result = llm.calls[1][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "boom" in tool_result["content"]


async def test_iteration_cap_returns_fallback():
    llm = FakeLLM([_tool_turn() for _ in range(2)])
    loop = AgentLoop(llm, FakeTools(), max_iterations=2)
    result = await loop.run("sys", [{"role": "user", "content": "u"}])
    assert result.text == _CAP_FALLBACK
    assert result.tools_used == ["t", "t"]


async def test_iteration_cap_prefers_last_text_over_fallback():
    llm = FakeLLM([_tool_turn(text="partial answer")])
    loop = AgentLoop(llm, FakeTools(), max_iterations=1)
    result = await loop.run("sys", [{"role": "user", "content": "u"}])
    assert result.text == "partial answer"


async def test_input_messages_are_not_mutated():
    llm = FakeLLM([_tool_turn(), AssistantTurn(text="done")])
    messages = [{"role": "user", "content": "u"}]
    await AgentLoop(llm, FakeTools()).run("sys", messages)
    assert messages == [{"role": "user", "content": "u"}]


async def test_empty_messages_rejected():
    with pytest.raises(ValueError):
        await AgentLoop(FakeLLM([]), FakeTools()).run("sys", [])
