# =============================================================================
# Brain module router — brain/modules.py
# =============================================================================
# WHAT: Discovers each configured module's tools (RegisterTools) and registers
#       them into Brain's ToolRegistry as PROXY tools — calling one forwards
#       over gRPC to the module (CallTool). After this, a module tool is
#       indistinguishable from a built-in tool to an agent: same MCP tools/list,
#       same tools/call, same access-list. A module that is unreachable at boot
#       is RETRIED in the background until it registers, and the whole set can
#       be re-discovered on demand (POST /admin/modules/refresh).
#
# WHY discover-then-proxy (pull): Brain calls each module's RegisterTools and
#       caches the result as registry entries. The module declares its tools;
#       Brain advertises them to agents. No push, no module needing Brain's
#       address — modules stay passive servers.
#
# WHY background retry (Step 2 of ARCHITECTURE_REVIEW.md): registration used to
#       happen exactly once at boot — compose only guarantees service_started
#       (not "gRPC bound"), so a module that came up one second slow silently
#       contributed ZERO tools until Brain was restarted. Now a failed module
#       stays "pending" and is retried every module_retry_seconds; adding a new
#       tool also no longer requires a Brain restart (refresh picks it up).
#
# WHY re-registration is idempotent per tool: a refresh re-asks LIVE modules
#       too, so a tool that is already registered to the SAME module is skipped
#       silently (not an error — it's the normal case), while a name owned by a
#       DIFFERENT module/built-in is a real clash and is logged and skipped.
#
# WHY the proxy handler reads the actor from the provenance ContextVar: the
#       registry's handler signature is handler(**arguments) — it has no agent
#       parameter. The MCP server publishes the acting agent per request
#       (current_actor_slug); the proxy reads it there and forwards it as the
#       CallTool agent_id. Same mechanism the memory tools use for provenance.
#
# WHY the proxy returns a ToolResult (Step 5): the gRPC reply already carries a
#       real is_error bool; passing it through as ToolResult(content, is_error)
#       preserves it end-to-end — the old "fold it into an 'Error:' prefix"
#       shim is gone, and a module tool whose legitimate output happens to
#       start with "Error:" is no longer misreported.
#
# HOW: `await ModuleRouter(modules, ModuleClient()).register_into(registry)` at
#      Brain startup; then `retry_pending_forever(registry, interval)` as a
#      background task; `refresh(registry)` from the admin endpoint.
# =============================================================================

import asyncio
import json
import logging

from infra.modkit import ToolResult

from brain.module_client import ModuleClient
from brain.provenance import actor_slug
from brain.registry import Tool, ToolRegistry

logger = logging.getLogger(__name__)


class ModuleRouter:
    """Registers configured modules' tools as gRPC-proxy tools, with retry."""

    def __init__(self, modules: list[tuple[str, str]], client: ModuleClient) -> None:
        # modules: list of (module_name, grpc_address).
        self._modules = modules
        self._client = client
        # Modules that have successfully registered at least once. A registered
        # module never becomes unregistered (its proxies keep working — a call
        # to a down module is an error RESULT, not a lost tool).
        self._registered: set[str] = set()
        # Serializes discovery passes: the boot pass, the background retry and
        # an admin refresh may otherwise interleave on the same registry.
        self._lock = asyncio.Lock()

    def pending(self) -> list[tuple[str, str]]:
        """Configured modules that have not registered yet."""
        return [(n, a) for n, a in self._modules if n not in self._registered]

    async def register_into(self, registry: ToolRegistry) -> int:
        """Discover every module's tools and register proxies. Returns how many
        tools were registered. A module that is unreachable is logged, kept as
        PENDING (the background retry picks it up) and skipped — Brain still
        serves its built-in tools and the other modules."""
        async with self._lock:
            total = 0
            for name, addr in self._modules:
                added = await self._register_module(registry, name, addr)
                total += added or 0
            return total

    async def retry_pending_forever(
        self, registry: ToolRegistry, interval_seconds: int
    ) -> None:
        """Background loop: keep retrying pending modules until all registered,
        then exit (nothing left to do — a registered module stays registered).
        One iteration's failure never stops the loop."""
        if not self.pending():
            return
        logger.info(
            "Module retry loop started (every %ds): pending %s",
            interval_seconds, [n for n, _ in self.pending()],
        )
        while self.pending():
            await asyncio.sleep(interval_seconds)
            try:
                async with self._lock:
                    for name, addr in self.pending():
                        await self._register_module(registry, name, addr)
            except Exception:
                logger.exception("Module retry pass failed; will retry")
        logger.info("Module retry loop done — all configured modules registered.")

    async def refresh(self, registry: ToolRegistry) -> dict:
        """Re-discover ALL configured modules now (admin-triggered): pending
        ones get another chance, live ones can contribute newly added tools.
        Returns a per-module summary for the admin response."""
        summary: dict[str, dict] = {}
        async with self._lock:
            for name, addr in self._modules:
                added = await self._register_module(registry, name, addr)
                if added is None:
                    summary[name] = {"status": "unreachable", "tools_added": 0}
                else:
                    summary[name] = {"status": "ok", "tools_added": added}
        return summary

    async def _register_module(
        self, registry: ToolRegistry, name: str, addr: str
    ) -> int | None:
        """One module's discovery pass. Returns how many NEW tools were added,
        or None if the module was unreachable. Idempotent: tools this module
        already registered are skipped silently. Callers hold self._lock."""
        try:
            specs = await self._client.register_tools(addr)
        except Exception:
            logger.warning("Module '%s' at %s unreachable — will retry", name, addr)
            return None
        added = 0
        for spec in specs:
            existing = registry.get(spec.name)
            if existing is not None:
                if existing.module == name:
                    continue  # already ours from an earlier pass — normal
                # Name owned by a built-in or another module: a real clash.
                # Skip this one tool (keep the existing owner) rather than let
                # one clashing name break the pass.
                logger.error(
                    "Module '%s' tool '%s' clashes with an existing tool "
                    "(module=%s) — skipping", name, spec.name, existing.module,
                )
                continue
            try:
                schema = json.loads(spec.input_schema_json or "{}")
            except json.JSONDecodeError:
                logger.error(
                    "Module '%s' tool '%s' has invalid input_schema_json — skipping",
                    name, spec.name,
                )
                continue
            registry.register(
                Tool(
                    name=spec.name,
                    description=spec.description,
                    input_schema=schema,
                    handler=self._make_proxy(addr, spec.name),
                    module=name,
                    # A module ships its own usage guidance (proto field 4);
                    # older modules simply send "" and contribute nothing.
                    usage=spec.usage,
                )
            )
            added += 1
        self._registered.add(name)
        if added:
            logger.info("Module '%s': %d tool(s) registered", name, added)
        return added

    def _make_proxy(self, addr: str, tool_name: str):
        """Build the handler that proxies this tool to its module over gRPC."""
        client = self._client

        async def proxy(**arguments) -> ToolResult:
            args_json = json.dumps(arguments)
            content, is_error = await client.call_tool(
                addr, tool_name, args_json, actor_slug()
            )
            # The gRPC bool rides through structurally — no prefix folding.
            return ToolResult(text=content, is_error=is_error)

        return proxy


def parse_modules(spec: str) -> list[tuple[str, str]]:
    """Parse the BRAIN_MODULES config string into (name, address) pairs.

    Format: "name=host:port,name2=host:port". Empty -> no modules (Brain serves
    only its built-in memory tools). Malformed entries are skipped with a warning
    so one typo doesn't stop Brain from booting."""
    modules: list[tuple[str, str]] = []
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            logger.warning("Ignoring malformed BRAIN_MODULES entry: %r", entry)
            continue
        name, _, addr = entry.partition("=")
        name, addr = name.strip(), addr.strip()
        if not name or not addr:
            logger.warning("Ignoring malformed BRAIN_MODULES entry: %r", entry)
            continue
        modules.append((name, addr))
    return modules
