# =============================================================================
# LLM client — agents/core/llm.py
# =============================================================================
# WHAT: The seam between the tool-use loop and Claude. Defines a provider-
#       agnostic LLMClient Protocol + a normalized AssistantTurn, and the
#       AnthropicClient that implements it with the official `anthropic` SDK.
#
# WHY a Protocol + normalized turn (and why the SDK lives ONLY here):
#       CLAUDE.md's strict stack confines the Anthropic SDK to Agent Core. By
#       putting it behind LLMClient, the loop never imports `anthropic` — it
#       depends on AssistantTurn (plain data). That keeps the loop unit-testable
#       with a fake client and leaves room for a CLI/Max backend later without
#       touching the loop (the monolith's app/services/llm proved this pattern).
#
# WHY AssistantTurn carries raw provider content blocks: the tool-use protocol
#       requires echoing the assistant's exact content back into the next
#       request (so tool_result blocks line up with tool_use ids). We keep those
#       blocks opaque in `content_blocks` and let the loop pass them through
#       verbatim, while exposing the parsed `text` and `tool_calls` for logic.
#
# WHY prompt caching is set up HERE (and why it needs `runtime` separate from
#       `system`): every request re-sends the tool schemas + the stable system
#       head (soul + protocol + usage notes) — ~20 KB that never changes — and a
#       tool-using turn does that up to MAX_ITERATIONS times. Anthropic caching
#       is OPT-IN: without an explicit cache_control marker you pay full input
#       price for all of it, every time. The agent used to hand us one
#       pre-concatenated string whose tail carried the clock and the recalled
#       memories, so even a marker would have been useless — the volatile tail
#       changed each turn and broke the prefix. Now `system` is the stable head
#       (marked ephemeral) and `runtime` follows it as an UNMARKED block, so the
#       cached prefix covers tools + head and survives between messages.
#
# HOW: `AnthropicClient(api_key, model).generate(system, messages, tools)` ->
#       AssistantTurn.
# =============================================================================

import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

from agents.core.history import Message
from agents.core.runner import TurnUsage

logger = logging.getLogger(__name__)

# Default max output tokens per model call, exported so the loop and tests share
# one number. (The tool-loop ITERATION cap is separate: loop.MAX_ITERATIONS.)
DEFAULT_MAX_TOKENS = 4096

# The cache marker we attach to a prefix we want kept warm between requests.
# "ephemeral" = Anthropic's ~5-minute TTL, refreshed on every hit — comfortably
# longer than the gap between messages in a live conversation.
_CACHE_CONTROL = {"type": "ephemeral"}


@dataclass
class ToolCall:
    """One tool the model asked to run this turn."""

    id: str          # tool_use id — the tool_result must echo it back
    name: str
    input: dict[str, Any]


@dataclass
class AssistantTurn:
    """A normalized model turn, provider-agnostic.

    `content_blocks` is the raw assistant content to echo back into the next
    request (opaque to the loop); `text` and `tool_calls` are the parsed view
    the loop reasons about."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    content_blocks: Any = None      # provider-native content, passed through
    stop_reason: str | None = None
    usage: TurnUsage | None = None  # what this single call cost


class LLMClient(Protocol):
    """One way of reaching Claude for an agentic turn."""

    def generate(
        self,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        *,
        runtime: str = "",
    ) -> Awaitable[AssistantTurn]:
        """One model call. `messages` is the running conversation (including
        prior tool_use/tool_result blocks); `tools` are MCP tool schemas.
        `system` is the STABLE prompt head and `runtime` this turn's volatile
        context — kept apart so the head stays cacheable (see the header)."""
        ...


class AnthropicClient:
    """LLMClient backed by the official Anthropic SDK.

    The SDK is imported lazily so importing agents.core (e.g. in tests that use a
    fake client) does not require `anthropic` to be installed."""

    def __init__(self, api_key: str, model: str) -> None:
        from anthropic import AsyncAnthropic  # lazy: SDK confined to this file

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate(
        self,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        *,
        runtime: str = "",
    ) -> AssistantTurn:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=_system_blocks(system, runtime),
            messages=_cache_last_message(messages),
            tools=tools,
        )
        return _to_turn(response)


def _system_blocks(system: str, runtime: str) -> list[dict[str, Any]]:
    """The system prompt as blocks, with the cache breakpoint after the stable
    head.

    Everything BEFORE a breakpoint is cached, and the request is laid out as
    tools -> system -> messages. So one marker here covers the tool schemas AND
    the head — the two biggest fixed costs — while `runtime` sits after it,
    free to change every message without invalidating anything."""
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": system, "cache_control": _CACHE_CONTROL}
    ]
    if runtime:
        blocks.append({"type": "text", "text": runtime})
    return blocks


def _cache_last_message(messages: list[Message]) -> list[Message]:
    """A second breakpoint at the end of the conversation, for the tool loop.

    WHY: within one turn the loop calls the model repeatedly, and each call
    re-sends every earlier message plus the new tool_result. Marking the last
    block means iteration N+1 reads iteration N's whole conversation from cache
    instead of reprocessing it — the saving grows with exactly the turns that
    are already the most expensive (deep research, many tool calls).

    The input is never mutated: we copy the last message and its content list,
    so the caller's history (and the loop's own msgs list) stay unmarked."""
    if not messages:
        return messages
    out = list(messages)
    last = dict(out[-1])
    content = last.get("content")
    if isinstance(content, str):
        # Stored history turns are plain strings; a marker needs a block.
        if not content:
            return messages  # empty content — leave it alone, the API rejects it
        last["content"] = [
            {"type": "text", "text": content, "cache_control": _CACHE_CONTROL}
        ]
    elif isinstance(content, list) and content:
        blocks = [dict(b) if isinstance(b, dict) else b for b in content]
        if not isinstance(blocks[-1], dict):
            return messages  # provider objects (echoed assistant content): skip
        blocks[-1] = {**blocks[-1], "cache_control": _CACHE_CONTROL}
        last["content"] = blocks
    else:
        return messages
    out[-1] = last
    return out


def _to_usage(response: Any) -> TurnUsage | None:
    """Map the response's `usage` into our provider-agnostic TurnUsage.

    Defensive throughout: usage is telemetry, and a missing/renamed field must
    never turn a good reply into an exception."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    def _n(name: str) -> int:
        value = getattr(usage, name, 0)
        return value if isinstance(value, int) else 0
    return TurnUsage(
        input_tokens=_n("input_tokens"),
        output_tokens=_n("output_tokens"),
        cache_read_tokens=_n("cache_read_input_tokens"),
        cache_write_tokens=_n("cache_creation_input_tokens"),
    )


def _to_turn(response: Any) -> AssistantTurn:
    """Map an Anthropic Messages response into an AssistantTurn.

    Kept as a free function (not a method) so it can be unit-tested against a
    hand-built response object without constructing a client."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))
    return AssistantTurn(
        text="".join(text_parts).strip(),
        tool_calls=tool_calls,
        content_blocks=response.content,
        stop_reason=getattr(response, "stop_reason", None),
        usage=_to_usage(response),
    )
