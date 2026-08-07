# =============================================================================
# Unit tests — the per-tool usage notes pipeline
# =============================================================================
# WHAT: the renderer (agents/core/tool_usage.py), Brain's MCP extension field
#       (brain/registry.py to_mcp_schema), and the client split that keeps
#       Anthropic tool schemas clean while exposing usage separately
#       (agents/core/mcp_client.py).
# WHY: the schemas from list_tools() go STRAIGHT into messages.create(tools=…),
#       where an unexpected key is an API error — so "usage never leaks into a
#       schema" is a correctness invariant, not a style preference.
# HOW: the renderer is pure; the client is driven against a real local aiohttp
#       server speaking Brain's JSON-RPC shape.
# =============================================================================

from aiohttp import web
from aiohttp.test_utils import TestServer

from agents.core.mcp_client import BrainMCPClient
from agents.core.tool_usage import render_tool_usage
from brain.registry import Tool

# ----- renderer ------------------------------------------------------------


def test_empty_notes_render_nothing():
    assert render_tool_usage([]) == ""
    # Blank/whitespace notes count as absent — no empty heading.
    assert render_tool_usage([("t", ""), ("u", "   ")]) == ""


def test_notes_are_sorted_for_prompt_cache_stability():
    block = render_tool_usage([("zeta", "z note"), ("alpha", "a note")])
    assert block.index("alpha") < block.index("zeta")
    # Same input in a different order must produce identical bytes.
    assert block == render_tool_usage([("alpha", "a note"), ("zeta", "z note")])


def test_note_body_and_header_present():
    block = render_tool_usage([("add_reminder", "pass an offset")])
    assert block.startswith("## How to use your tools")
    assert "- **add_reminder** — pass an offset" in block


def test_multiline_note_is_indented_under_its_bullet():
    block = render_tool_usage([("t", "line one\nline two")])
    assert "- **t** — line one\n  line two" in block


# ----- Brain's MCP extension ----------------------------------------------


async def _handler() -> str:
    return "ok"


def test_to_mcp_schema_omits_usage_when_absent():
    schema = Tool("t", "d", {"type": "object"}, _handler).to_mcp_schema()
    assert set(schema) == {"name", "description", "inputSchema"}


def test_to_mcp_schema_includes_usage_when_present():
    schema = Tool("t", "d", {"type": "object"}, _handler, usage="call me twice").to_mcp_schema()
    assert schema["usage"] == "call me twice"


# ----- client: schemas stay clean, usage comes separately -------------------


class FakeBrainServer:
    """Minimal Brain: answers tools/list with two tools, one carrying usage."""

    TOOLS = [
        {"name": "b_tool", "description": "b", "inputSchema": {"type": "object"}},
        {
            "name": "a_tool",
            "description": "a",
            "inputSchema": {"type": "object"},
            "usage": "call a_tool like this",
        },
    ]

    def __init__(self):
        self.calls = 0

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/mcp", self._mcp)
        return app

    async def _mcp(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.calls += 1
        return web.json_response(
            {"jsonrpc": "2.0", "id": body.get("id"), "result": {"tools": self.TOOLS}}
        )


async def _client() -> tuple[BrainMCPClient, TestServer, FakeBrainServer]:
    brain = FakeBrainServer()
    server = TestServer(brain.app())
    await server.start_server()
    return BrainMCPClient(str(server.make_url("/")), "tok"), server, brain


async def test_list_tools_returns_only_anthropic_fields():
    client, server, _ = await _client()
    try:
        tools = await client.list_tools()
    finally:
        await server.close()
    for tool in tools:
        # An extra key here is an Anthropic API error at request time.
        assert set(tool) == {"name", "description", "input_schema"}


async def test_usage_notes_expose_only_tools_that_declare_usage():
    client, server, _ = await _client()
    try:
        notes = await client.usage_notes()
    finally:
        await server.close()
    assert notes == [("a_tool", "call a_tool like this")]


async def test_schemas_and_notes_share_one_cached_fetch():
    client, server, brain = await _client()
    try:
        await client.list_tools()
        await client.usage_notes()
        await client.list_tools()
    finally:
        await server.close()
    assert brain.calls == 1  # the tool set is frozen for a session


async def test_force_refetches():
    client, server, brain = await _client()
    try:
        await client.list_tools()
        await client.list_tools(force=True)
    finally:
        await server.close()
    assert brain.calls == 2
