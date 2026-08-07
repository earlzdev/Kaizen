# =============================================================================
# Brain module gRPC client — brain/module_client.py
# =============================================================================
# WHAT: Brain's gRPC client toward modules — the "gRPC к модулям" half. Speaks
#       the frozen Module contract (proto/module.proto): RegisterTools to learn a
#       module's tools, CallTool to run one. This is what makes Brain a proxy in
#       front of the modules.
#
# WHY Brain is the client (module is the server): the v2 flow is agent -> Brain
#       (MCP) -> module (gRPC). Brain dials modules; modules never dial Brain.
#       Same direction the Phase-0 health ping already proved.
#
# WHY a channel per call (for now): simple and correct — matches the Phase-0
#       self-test. Modules are few and calls are not hot enough yet to need a
#       pooled, long-lived channel; that optimization can come with real load.
#
# WHY agent_id is forwarded: Brain has already authenticated the agent and
#       enforced the access-list; it passes the resolved identity so the module
#       can attribute work. The module TRUSTS this field (Brain set it).
#
# HOW: `await ModuleClient().register_tools(addr)` -> list[ToolSpec];
#      `await ModuleClient().call_tool(addr, name, args_json, agent_id)`.
# =============================================================================

import logging

import grpc

from infra.proto.gen import module_pb2, module_pb2_grpc

logger = logging.getLogger(__name__)

# Brain's contract version, sent on RegisterTools for future negotiation.
BRAIN_CONTRACT_VERSION = "1.0.0"


class ModuleClient:
    """Talks the Module gRPC contract to any module by address."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def register_tools(self, addr: str) -> list[module_pb2.ToolSpec]:
        """Ask a module for the tools it offers. Raises AioRpcError if the
        module is unreachable — the caller decides whether that is fatal."""
        async with grpc.aio.insecure_channel(addr) as channel:
            stub = module_pb2_grpc.ModuleStub(channel)
            reply = await stub.RegisterTools(
                module_pb2.RegisterToolsRequest(brain_version=BRAIN_CONTRACT_VERSION),
                timeout=self._timeout,
            )
            logger.info(
                "Module '%s' at %s registered %d tool(s)",
                reply.module, addr, len(reply.tools),
            )
            return list(reply.tools)

    async def call_tool(
        self, addr: str, name: str, arguments_json: str, agent_id: str
    ) -> tuple[str, bool]:
        """Run a tool on a module -> (content, is_error)."""
        async with grpc.aio.insecure_channel(addr) as channel:
            stub = module_pb2_grpc.ModuleStub(channel)
            reply = await stub.CallTool(
                module_pb2.CallToolRequest(
                    name=name, arguments_json=arguments_json, agent_id=agent_id
                ),
                timeout=self._timeout,
            )
            return reply.content, reply.is_error
