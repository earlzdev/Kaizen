# =============================================================================
# Runner seam — agents/core/runner.py
# =============================================================================
# WHAT: The abstraction for "turn a system prompt + conversation into a final
#       reply." Two implementations exist:
#         - AgentLoop (loop.py) — the API backend: WE drive the tool-use loop,
#           calling the model (LLMClient) and Brain's tools (ToolSource) each turn.
#         - ClaudeCliRunner (cli.py) — the Max/CLI backend: the `claude` CLI runs
#           its OWN tool loop, reaching Brain's tools over MCP directly.
#
# WHY a seam (not just LLMClient): the CLI subsumes the whole loop, so it can't
#       plug in as a single-call LLMClient — it IS the loop. Both backends share
#       this one interface, so Agent (agent.py) and every agent built on Agent
#       Core can switch backends without changing anything else.
#
# WHY run() returns RunResult (not a bare string): the Agent needs to know
#       WHICH tools a turn actually used — that is what decides whether the
#       self-verification pass runs (a turn that searched the web gets
#       fact-checked; small talk doesn't pay that cost).
#
# WHY on_status: a research turn can take a minute+. The connector passes a
#       callback so it can show the owner live progress ("searching…",
#       "reading page…") instead of a silent hang. It is optional and
#       best-effort — a runner must work fine with on_status=None, and a
#       failing callback must never break the turn.
#
# WHY run() takes `system` and `runtime` SEPARATELY (the whole prompt-cache
#       story): `system` is the stable head — soul, protocol, tool usage notes —
#       byte-identical between turns. `runtime` is the volatile block (the
#       clock, the owner profile, memories recalled for THIS message). Both end
#       up in front of the model, but a backend must be free to put them in
#       different places so the expensive stable part stays cacheable:
#       AnthropicClient makes `system` a cache-marked block and `runtime` an
#       uncached one after it; the CLI runner keeps `system` (and only `system`)
#       in --append-system-prompt and moves `runtime` into the prompt body. When
#       the two were concatenated into one string, the volatile tail changed
#       every message and the cached prefix broke at the soul boundary — we paid
#       full price for ~20 KB of unchanged text on every single turn.
#
# WHY run() takes `resume`: some backends keep a server-side session (the CLI
#       does). Handing back a prior RunResult.session_id lets a follow-up turn —
#       today the self-check gate — continue that session instead of replaying
#       the whole transcript and system prompt from scratch. Backends without
#       sessions ignore it; they already receive the full messages list.
#
# HOW: Agent is constructed with a Runner; AgentLoop and ClaudeCliRunner both
#      satisfy it.
# =============================================================================

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from agents.core.history import Message

# (tool_name, detail) — e.g. ("find_online", "best e-ink readers 2026").
# tool_name is the bare tool name (no MCP server prefix); detail is a short
# human-relevant argument (query/url), possibly empty.
StatusCallback = Callable[[str, str], Awaitable[None]]


@dataclass
class TurnUsage:
    """What one turn cost, in tokens (and dollars, when the backend says so).

    WHY this exists at all: before it, nothing in the system recorded token
    consumption — the Anthropic response's `usage` was dropped on the floor and
    the CLI's result event ignored — so the only visible signal that a turn was
    expensive was the Max subscription running out. You cannot tune what you
    cannot see; every runner now fills this in and the Agent logs it per turn.

    `cache_read` vs `cache_write` is the number that matters for tuning: a
    healthy steady state has cache_read ≫ input on every turn after the first.
    If cache_read stays 0, the stable prefix is being rebuilt each time and
    something upstream is mixing volatile text into it."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0      # prefix served from cache (~10% of input price)
    cache_write_tokens: int = 0     # prefix written INTO the cache (~125%)
    cost_usd: float | None = None   # the CLI reports this; the API does not

    def __add__(self, other: "TurnUsage | None") -> "TurnUsage":
        """Sum two turns (a draft + its self-check gate) into one line."""
        if other is None:
            return self
        cost = None
        if self.cost_usd is not None or other.cost_usd is not None:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        return TurnUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=cost,
        )

    def summary(self) -> str:
        """One compact log line."""
        parts = [
            f"in={self.input_tokens}",
            f"out={self.output_tokens}",
            f"cache_read={self.cache_read_tokens}",
            f"cache_write={self.cache_write_tokens}",
        ]
        if self.cost_usd is not None:
            parts.append(f"cost=${self.cost_usd:.4f}")
        return " ".join(parts)


@dataclass
class RunResult:
    """One finished agentic turn: the reply + which tools it called."""

    text: str
    tools_used: list[str] = field(default_factory=list)  # bare names, call order
    # What the turn cost, when the backend reported it (None = unknown).
    usage: TurnUsage | None = None
    # Backend-side session handle, when the backend keeps one (the CLI does).
    # Pass it back as run(resume=...) to continue instead of replaying.
    session_id: str | None = None


def status_detail(tool_input: dict) -> str:
    """The one short argument worth showing the owner for a tool call (query,
    URL, …), or ''. Shared by both runners so statuses look the same."""
    for key in ("query", "url", "text", "file_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    return ""


class Runner(Protocol):
    """Runs one agentic turn: (system prompt, message history) -> RunResult."""

    def run(
        self,
        system: str,
        messages: list[Message],
        on_status: StatusCallback | None = None,
        *,
        runtime: str = "",
        resume: str | None = None,
    ) -> Awaitable[RunResult]:
        """`system` is the STABLE head (cache it); `runtime` is this turn's
        volatile context and must never be concatenated into `system`.
        `resume` optionally continues a backend session from a prior
        RunResult.session_id — backends without sessions ignore it."""
        ...
