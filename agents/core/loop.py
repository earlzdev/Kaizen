# =============================================================================
# Agent tool-use loop — agents/core/loop.py
# =============================================================================
# WHAT: The Claude tool-use loop. Given a system prompt and a running message
#       list, it calls the model; while the model asks for tools, it runs them
#       through a ToolSource (Brain's MCP client) and feeds the results back,
#       until the model produces a final text answer.
#
# WHY a loop and not one call: Claude's tool use is turn-based — it may call
#       several tools (recall a memory, set a reminder) before it has enough to
#       answer. Each iteration is model -> tool_use -> we run it -> tool_result
#       -> model again. Ported from the monolith's run_agent (principle 3).
#
# WHY ToolSource is an injected Protocol: the loop must not know that tools come
#       from Brain over MCP. Anything that can list tool schemas and execute a
#       tool by name satisfies it — the real BrainMCPClient in production, a fake
#       in tests. This is what makes the loop unit-testable with no network.
#
# WHY a hard iteration cap: a model that keeps calling tools without answering
#       would loop forever and burn tokens. After MAX_ITERATIONS we stop and
#       return the best text we have (or a clear fallback), never hang.
#
# WHY tool failures don't crash the turn: a tool result carries is_error, which
#       we pass back to the model as an error tool_result. The model then reacts
#       (apologizes, tries another way) instead of the whole reply dying.
# =============================================================================

import logging
from collections.abc import Awaitable
from typing import Any, Protocol

from agents.core.history import Message
from agents.core.llm import DEFAULT_MAX_TOKENS, LLMClient
from agents.core.runner import RunResult, StatusCallback, TurnUsage, status_detail

logger = logging.getLogger(__name__)

# Max model<->tool round trips before we force a final answer.
MAX_ITERATIONS = 10

# Returned when the loop hits the iteration cap without a final text answer.
_CAP_FALLBACK = (
    "I wasn't able to finish that in a reasonable number of steps. "
    "Could you rephrase or narrow it down?"
)


class ToolSource(Protocol):
    """Where the loop gets tool schemas and runs tools. Brain's MCP client in
    production; a fake in tests."""

    def list_tools(self) -> Awaitable[list[dict[str, Any]]]:
        """Tool schemas in Anthropic tool format
        ({name, description, input_schema})."""
        ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Awaitable[tuple[str, bool]]:
        """Run a tool, returning (result_text, is_error)."""
        ...


class AgentLoop:
    """Runs one agentic turn to completion (all tool calls resolved)."""

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolSource,
        *,
        max_iterations: int = MAX_ITERATIONS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._max_iterations = max_iterations
        self._max_tokens = max_tokens

    async def run(
        self,
        system: str,
        messages: list[Message],
        on_status: StatusCallback | None = None,
        *,
        runtime: str = "",
        resume: str | None = None,
    ) -> RunResult:
        """Drive the loop from a system prompt + conversation history to a final
        text answer. `messages` is copied, not mutated in place.

        `runtime` is this turn's volatile context, passed to the client
        alongside (never merged into) the cacheable `system` head. `resume` is
        accepted and ignored: this backend keeps no server-side session — the
        caller already hands us the full conversation every time."""
        if not messages:
            raise ValueError("messages cannot be empty")

        tool_schemas = await self._tools.list_tools()
        msgs: list[Message] = [dict(m) for m in messages]

        last_text = ""
        tools_used: list[str] = []
        # Summed across iterations: one "turn" the Agent reasons about can be
        # ten model calls, and only the total is meaningful for cost.
        usage = TurnUsage()
        for i in range(self._max_iterations):
            turn = await self._llm.generate(
                system, msgs, tool_schemas, self._max_tokens, runtime=runtime
            )
            usage = usage + turn.usage
            if turn.text:
                last_text = turn.text

            # No tool calls -> the model is done; return its text.
            if not turn.tool_calls:
                return RunResult(turn.text or last_text, tools_used, usage=usage)

            # Echo the assistant's exact content back, then answer each tool.
            msgs.append({"role": "assistant", "content": turn.content_blocks})
            results = []
            for call in turn.tool_calls:
                logger.info("Tool call: %s(%s)", call.name, call.input)
                tools_used.append(call.name)
                if on_status is not None:
                    # Progress is cosmetic — a broken callback must not kill
                    # the turn.
                    try:
                        await on_status(call.name, status_detail(call.input))
                    except Exception:
                        logger.exception("on_status callback failed")
                try:
                    text, is_error = await self._tools.call_tool(call.name, call.input)
                except Exception as e:  # a transport failure is an error result,
                    logger.exception("Tool '%s' raised", call.name)  # not a crash
                    text, is_error = f"Error: tool '{call.name}' failed: {e}", True
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": text,
                        "is_error": is_error,
                    }
                )
            msgs.append({"role": "user", "content": results})

        logger.warning("Agent loop hit iteration cap (%d)", self._max_iterations)
        return RunResult(last_text or _CAP_FALLBACK, tools_used, usage=usage)
