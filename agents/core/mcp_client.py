# =============================================================================
# Brain MCP client — agents/core/mcp_client.py
# =============================================================================
# WHAT: The agent's client to Brain over MCP (the frozen agent<->Brain contract,
#       docs/contracts/mcp-agent-brain.md). Speaks JSON-RPC to Brain's POST /mcp
#       with the agent's bearer token, and exposes:
#         - initialize()            handshake
#         - list_tools()            tool schemas (cached — they don't change mid-run)
#         - call_tool(name, args)   run a tool -> (text, is_error)
#         - recall(query)           convenience wrapper over the recall_memory tool,
#                                   with a short TTL cache (see below)
#       It is the production ToolSource the loop drives.
#
# WHY tools/list is cached: the contract freezes listChanged=false, so the tool
#       set is stable for a session. Fetching it once and reusing it saves a
#       network round trip on every turn.
#
# WHY recall is cached with a short TTL (the plan's explicit latency risk):
#       "recall is now a network call before every reply — cache it on the
#       Agent Core side". Memory changes slowly relative to a burst of messages, so we
#       cache recall results per normalized query for RECALL_TTL_SECONDS. This
#       trades a little staleness for skipping a network+embedding round trip on
#       every reply. The cache is invalidated whenever the agent WRITES memory
#       (remember/forget), so the agent never reads its own stale writes.
#
# WHY tool results map isError -> the bool the loop expects: Brain returns MCP
#       content + isError; we flatten content to text and pass isError through,
#       so a denied/failed tool becomes an error result the model can react to.
#
# HOW: `BrainMCPClient(base_url, token)`; `await client.list_tools()`,
#       `await client.call_tool("remember_fact", {"fact": "..."})`.
# =============================================================================

import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"
# How long a recall result stays fresh in the cache.
RECALL_TTL_SECONDS = 60.0
# Tool names that WRITE shared memory — calling any invalidates the recall cache.
_MEMORY_WRITE_TOOLS = frozenset({"remember_fact", "forget_memory", "set_profile"})


class BrainMCPError(Exception):
    """Brain returned a JSON-RPC error (bad token, unsupported method, ...)."""


class BrainMCPClient:
    """Async MCP client to Brain. One instance per agent (holds its token)."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        # Normalize so base_url + "/mcp" is well-formed regardless of trailing /.
        self._base = base_url.rstrip("/")
        self._mcp_url = self._base + "/mcp"
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._next_id = 0
        self._tools_cache: list[dict[str, Any]] | None = None
        self._recall_cache: dict[str, tuple[float, str]] = {}

    # ----- JSON-RPC plumbing ----------------------------------------------
    @property
    def token(self) -> str:
        """The bearer token this client authenticates with (e.g. for handing the
        same identity to the claude CLI runner)."""
        return self._token

    def _rpc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        """One JSON-RPC call to Brain. Raises BrainMCPError on a JSON-RPC error
        or a non-200 HTTP status."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._rpc_id(),
            "method": method,
            "params": params or {},
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(self._mcp_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise BrainMCPError(f"Brain HTTP {resp.status}: {body[:200]}")
                data = await resp.json()
        if "error" in data:
            err = data["error"]
            raise BrainMCPError(f"Brain error {err.get('code')}: {err.get('message')}")
        return data.get("result", {})

    # ----- MCP surface -----------------------------------------------------
    async def initialize(self) -> dict:
        """MCP handshake. Returns Brain's serverInfo/capabilities."""
        return await self._rpc(
            "initialize", {"protocolVersion": MCP_PROTOCOL_VERSION}
        )

    async def list_tools(self, *, force: bool = False) -> list[dict[str, Any]]:
        """Tool schemas in Anthropic format ({name, description, input_schema}).

        Cached after the first call (the contract freezes the tool set for a
        session); pass force=True to refetch. Brain returns MCP `inputSchema`
        (camelCase); we map it to `input_schema` for the Anthropic tool format."""
        raw = await self._fetch_tools(force=force)
        # STRICTLY the Anthropic tool format: these dicts go straight into
        # messages.create(tools=...), and an unexpected key is an API error.
        # Brain's `usage` extension is therefore exposed separately, by
        # usage_notes() below — it is prompt material, not schema.
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {"type": "object", "properties": {}}),
            }
            for t in raw
        ]

    async def usage_notes(self, *, force: bool = False) -> list[tuple[str, str]]:
        """(tool_name, usage) for every visible tool that ships a usage note.

        Brain has ALREADY filtered tools/list by this agent's access-list, so
        the result is exactly what THIS agent may call — the usage block the
        agent renders is per-agent for free."""
        raw = await self._fetch_tools(force=force)
        return [(t["name"], t["usage"]) for t in raw if t.get("usage")]

    async def _fetch_tools(self, *, force: bool = False) -> list[dict[str, Any]]:
        """Brain's raw tools/list entries, cached (the contract freezes the
        tool set for a session)."""
        if self._tools_cache is not None and not force:
            return self._tools_cache
        result = await self._rpc("tools/list")
        self._tools_cache = list(result.get("tools", []))
        return self._tools_cache

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool -> (result_text, is_error). Flattens MCP text content.

        Invalidates the recall cache on any memory-write tool, so the agent
        never reads its own stale writes."""
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        text = "\n".join(
            block.get("text", "") for block in content if block.get("type") == "text"
        )
        is_error = bool(result.get("isError", False))
        if name in _MEMORY_WRITE_TOOLS and not is_error:
            self._recall_cache.clear()
        return text, is_error

    async def register_delivery_addr(self, delivery_addr: str) -> None:
        """Tell Brain where it can PUSH events to this agent (Phase 6). Auth is
        this agent's own bearer token (Brain's POST /agent/delivery). Raises
        BrainMCPError on a non-2xx so a misconfigured callback fails loudly."""
        url = self._base + "/agent/delivery"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(
                url, json={"delivery_addr": delivery_addr}, headers=headers
            ) as resp:
                if resp.status >= 400:
                    body = (await resp.text())[:200]
                    raise BrainMCPError(f"register delivery HTTP {resp.status}: {body}")

    async def recall(self, query: str) -> str:
        """Recall memories relevant to `query`, cached for RECALL_TTL_SECONDS.

        This is the hot path the plan warns about — called before (almost)
        every reply. The cache turns a burst of related messages into ONE
        network+embedding round trip."""
        key = query.strip().lower()
        now = time.monotonic()
        cached = self._recall_cache.get(key)
        if cached is not None and (now - cached[0]) < RECALL_TTL_SECONDS:
            return cached[1]
        text, is_error = await self.call_tool("recall_memory", {"query": query})
        if not is_error:
            self._recall_cache[key] = (now, text)
        return text
