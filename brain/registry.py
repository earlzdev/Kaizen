# =============================================================================
# Brain tool registry — brain/registry.py
# =============================================================================
# WHAT: Brain's own Tool type + registry. Mirrors the monolith's
#       app/core/registry.py, but two things differ deliberately:
#         1. handlers take NO user_id — Brain is single-tenant (one owner), so
#            there is no per-user routing; a handler just receives its args.
#         2. every Tool carries a `module` label (None for Brain's built-in
#            memory tools). That label is what the access-list keys on.
#
# WHY a Brain-owned copy instead of importing app.core.registry:
#   The plan isolates services — Brain must not import the v1 monolith's code,
#   or the two can never be split apart (mono-repo goal). This is the same
#   plugin idea, re-homed in Brain. It is small and self-contained on purpose.
#
# HOW: memory tools register with module=None; from Phase 4, module tools
#   discovered over gRPC register with module="mentor" etc., and the SAME
#   registry serves both to agents over MCP.
# =============================================================================

import logging
from dataclasses import dataclass, field
from typing import Any

from infra.modkit import ToolHandler, ToolResult, to_result, validate_args

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """One capability Brain exposes to agents over MCP."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    # Which module owns this tool. None = Brain built-in (memory/profile/
    # reminders). The access-list keys on (module, name).
    module: str | None = None
    # OPTIONAL "how to call me well" note (see infra.modkit.ToolDef.usage).
    usage: str = ""

    def to_mcp_schema(self) -> dict[str, Any]:
        """Render as an MCP tools/list entry.

        `usage` is a Kaizen EXTENSION to the MCP tool object, emitted only when
        non-empty: our own client reads it to build the agent's usage block,
        and any standard MCP client (the `claude` CLI) ignores unknown keys."""
        schema = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.usage:
            schema["usage"] = self.usage
        return schema


@dataclass
class ToolRegistry:
    """Collects tools and executes them by name."""

    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s (module=%s)", tool.name, tool.module)

    def register_all(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        """All tools, sorted by name — a stable order keeps the agent's prompt
        cache valid between requests."""
        return sorted(self._tools.values(), key=lambda t: t.name)

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Run a tool and return a structured ToolResult (Step 5 of
        ARCHITECTURE_REVIEW.md — no more "Error:" sniffing at call sites).

        Arguments are validated against the tool's declared input_schema BEFORE
        the ** splat, so a model-invented argument becomes a precise, fixable
        error message instead of a TypeError. Errors are returned (never
        raised) so the MCP layer hands them to the agent as an isError result —
        the agent then reacts instead of the whole turn dying. Handlers may
        return str (legacy — the "Error:" prefix rule applies via to_result)
        or a ToolResult."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"Error: unknown tool '{name}'", is_error=True)
        problem = validate_args(tool.input_schema, arguments)
        if problem is not None:
            return ToolResult(
                f"Error: invalid arguments for '{name}': {problem}", is_error=True
            )
        try:
            return to_result(await tool.handler(**arguments))
        except Exception as e:
            logger.exception("Tool '%s' failed", name)
            return ToolResult(f"Error: tool '{name}' failed: {e}", is_error=True)
