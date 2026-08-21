# =============================================================================
# Unit tests — brain/modules.py (module discovery, retry, refresh)
# =============================================================================
# WHAT: ModuleRouter over a fake gRPC client: boot registration, the pending/
#       retry mechanics (Step 2 of ARCHITECTURE_REVIEW.md), idempotent refresh,
#       clash handling, and parse_modules.
# WHY: registration used to happen once at boot — a module that booted a second
#       slow silently lost all its tools until a Brain restart (§2.2-3).
# HOW: FakeModuleClient scripts register_tools per address — no gRPC, no
#       network; the real ToolRegistry is used (it is pure in-memory).
# =============================================================================

import asyncio
import json
from types import SimpleNamespace

from brain.modules import ModuleRouter, parse_modules
from brain.registry import Tool, ToolRegistry


def spec(name, schema=None, usage=""):
    """Stands in for a proto ToolSpec — which, being proto3, ALWAYS carries
    every field (absent usage arrives as "")."""
    return SimpleNamespace(
        name=name,
        description=f"{name} tool",
        input_schema_json=json.dumps(schema if schema is not None else {"type": "object"}),
        usage=usage,
    )


class FakeModuleClient:
    """register_tools scripted per address; an address in `down` raises."""

    def __init__(self, tools_by_addr, down=()):
        self._tools = tools_by_addr
        self.down = set(down)
        self.register_calls = []
        self.call_timeouts = []

    async def register_tools(self, addr):
        self.register_calls.append(addr)
        if addr in self.down:
            raise ConnectionError(f"unreachable {addr}")
        return self._tools[addr]

    async def call_tool(self, addr, name, args_json, agent_id, *, timeout=None):
        self.call_timeouts.append((name, timeout))
        return f"ran {name}", False


async def test_boot_registers_reachable_and_keeps_unreachable_pending():
    client = FakeModuleClient(
        {"m:1": [spec("a"), spec("b")], "t:2": [spec("c")]}, down={"t:2"}
    )
    registry = ToolRegistry()
    router = ModuleRouter([("mentor", "m:1"), ("tracker", "t:2")], client)
    added = await router.register_into(registry)
    assert added == 2
    assert {t.name for t in registry.all()} == {"a", "b"}
    assert router.pending() == [("tracker", "t:2")]


async def test_refresh_registers_a_module_that_came_up_late():
    client = FakeModuleClient({"t:2": [spec("c")]}, down={"t:2"})
    registry = ToolRegistry()
    router = ModuleRouter([("tracker", "t:2")], client)
    await router.register_into(registry)
    assert registry.all() == []

    client.down.clear()  # the module is up now
    summary = await router.refresh(registry)
    assert summary == {"tracker": {"status": "ok", "tools_added": 1}}
    assert registry.get("c") is not None
    assert router.pending() == []


async def test_refresh_is_idempotent_and_picks_up_only_new_tools():
    client = FakeModuleClient({"m:1": [spec("a")]})
    registry = ToolRegistry()
    router = ModuleRouter([("mentor", "m:1")], client)
    await router.register_into(registry)

    # Second pass with the same tool + one new tool: only the new one is added,
    # and the already-registered one is NOT treated as a clash.
    client._tools["m:1"] = [spec("a"), spec("new")]
    summary = await router.refresh(registry)
    assert summary["mentor"] == {"status": "ok", "tools_added": 1}
    assert {t.name for t in registry.all()} == {"a", "new"}


async def test_refresh_reports_unreachable_module():
    client = FakeModuleClient({}, down={"m:1"})
    router = ModuleRouter([("mentor", "m:1")], client)
    summary = await router.refresh(ToolRegistry())
    assert summary == {"mentor": {"status": "unreachable", "tools_added": 0}}


async def test_clash_with_other_owner_is_skipped():
    async def builtin_handler() -> str:
        return "builtin"

    registry = ToolRegistry()
    registry.register(
        Tool(name="a", description="", input_schema={}, handler=builtin_handler, module=None)
    )
    client = FakeModuleClient({"m:1": [spec("a"), spec("b")]})
    router = ModuleRouter([("mentor", "m:1")], client)
    added = await router.register_into(registry)
    assert added == 1  # only "b"; "a" stays owned by the built-in
    assert registry.get("a").module is None


async def test_module_usage_note_reaches_the_registry():
    """A module ships its own usage guidance (proto field 4) — Brain must
    carry it into the registry so the agent can render it."""
    client = FakeModuleClient({"m:1": [spec("a", usage="call me twice"), spec("b")]})
    registry = ToolRegistry()
    await ModuleRouter([("mentor", "m:1")], client).register_into(registry)
    assert registry.get("a").usage == "call me twice"
    assert registry.get("b").usage == ""
    assert "usage" not in registry.get("b").to_mcp_schema()


async def test_invalid_schema_json_skips_only_that_tool():
    bad = SimpleNamespace(name="bad", description="", input_schema_json="{not json", usage="")
    client = FakeModuleClient({"m:1": [bad, spec("good")]})
    registry = ToolRegistry()
    added = await ModuleRouter([("mentor", "m:1")], client).register_into(registry)
    assert added == 1
    assert registry.get("good") is not None
    assert registry.get("bad") is None


async def test_retry_loop_exits_immediately_when_nothing_pending():
    client = FakeModuleClient({"m:1": [spec("a")]})
    registry = ToolRegistry()
    router = ModuleRouter([("mentor", "m:1")], client)
    await router.register_into(registry)
    # Must return, not loop — guard with a timeout.
    await asyncio.wait_for(router.retry_pending_forever(registry, 1), timeout=1.0)


async def test_retry_loop_registers_a_module_that_comes_up_late():
    """The unit-level stand-in for the docker verification criterion: stop the
    module, start it later, its tools appear WITHOUT restarting Brain."""
    client = FakeModuleClient({"t:2": [spec("c")]}, down={"t:2"})
    registry = ToolRegistry()
    router = ModuleRouter([("tracker", "t:2")], client)
    await router.register_into(registry)
    assert router.pending() == [("tracker", "t:2")]

    task = asyncio.create_task(router.retry_pending_forever(registry, 0))
    await asyncio.sleep(0.01)  # let at least one failing retry pass run
    client.down.clear()  # the module "container" is up now
    # The loop must register it and then EXIT (nothing pending anymore).
    await asyncio.wait_for(task, timeout=1.0)
    assert registry.get("c") is not None
    assert router.pending() == []


async def test_registered_proxy_forwards_to_the_module():
    client = FakeModuleClient({"m:1": [spec("a")]})
    registry = ToolRegistry()
    await ModuleRouter([("mentor", "m:1")], client).register_into(registry)
    result = await registry.execute("a", {"x": 1})
    assert (result.text, result.is_error) == ("ran a", False)


async def test_browser_backed_tools_get_the_long_timeout():
    client = FakeModuleClient({"t:1": [spec("read_page"), spec("mentor_search")]})
    registry = ToolRegistry()
    await ModuleRouter([("tools", "t:1")], client).register_into(registry)
    await registry.execute("read_page", {})
    await registry.execute("mentor_search", {})
    assert dict(client.call_timeouts) == {"read_page": 25.0, "mentor_search": None}


def test_parse_modules_happy_and_malformed():
    assert parse_modules("mentor=m:9102, tracker=t:9103") == [
        ("mentor", "m:9102"), ("tracker", "t:9103"),
    ]
    assert parse_modules("") == []
    assert parse_modules("oops,=x:1,name=") == []
