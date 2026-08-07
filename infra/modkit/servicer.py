# =============================================================================
# Shared Module servicer — infra/modkit/servicer.py
# =============================================================================
# WHAT: THE gRPC servicer for the frozen Module contract: RegisterTools
#       (declare the module's ToolDefs) and CallTool (validate args against the
#       tool's schema, dispatch, normalize the outcome). Used by tools/, mentor
#       and tracker — the three byte-identical copies it replaces are gone.
#
# WHY validation happens HERE (before the ** splat): the declared input_schema
#       used to be decorative — the model could invent an argument and the
#       handler blew up with a TypeError surfaced as a generic failure. Now the
#       schema is enforced at the door and the model gets a precise, fixable
#       message ("unknown argument(s): X; expected: ...").
#
# WHY errors become is_error replies (not exceptions): a bad tool name,
#       malformed args, or a handler failure returns CallToolReply(
#       is_error=True) with a message, so Brain forwards an MCP error result
#       and the agent reacts — the service never crashes the call.
#
# WHY agent_id is logged, not enforced: Brain already authenticated the agent
#       and checked the access-list before proxying. Modules trust the
#       forwarded identity; they record it for context.
#
# HOW: `ModuleServicer("mentor", build_tools(store))` added to a grpc.aio
#      server in each module's main.py.
# =============================================================================

import json
import logging

import grpc

from infra.modkit.tooldef import ToolDef, to_result, validate_args
from infra.proto.gen import module_pb2, module_pb2_grpc

logger = logging.getLogger(__name__)


def _error(message: str) -> module_pb2.CallToolReply:
    return module_pb2.CallToolReply(content=message, is_error=True)


class ModuleServicer(module_pb2_grpc.ModuleServicer):
    """Serves a module's ToolDefs over the Module gRPC contract."""

    def __init__(self, module_name: str, tools: list[ToolDef]) -> None:
        self._module = module_name
        self._tools: dict[str, ToolDef] = {}
        for tool in tools:
            if tool.name in self._tools:
                # First one wins, loudly — the old copies silently last-won.
                logger.error(
                    "Duplicate tool name '%s' in module '%s' — keeping the first",
                    tool.name, module_name,
                )
                continue
            self._tools[tool.name] = tool

    @property
    def tools(self) -> dict[str, ToolDef]:
        """The registered tools (read-only by convention; for boot logging)."""
        return self._tools

    async def RegisterTools(
        self,
        request: module_pb2.RegisterToolsRequest,
        context: grpc.aio.ServicerContext,
    ) -> module_pb2.RegisterToolsReply:
        logger.info("RegisterTools called by Brain (v=%s)", request.brain_version)
        specs = [
            module_pb2.ToolSpec(
                name=t.name,
                description=t.description,
                input_schema_json=json.dumps(t.input_schema),
                usage=t.usage,
            )
            for t in self._tools.values()
        ]
        return module_pb2.RegisterToolsReply(module=self._module, tools=specs)

    async def CallTool(
        self,
        request: module_pb2.CallToolRequest,
        context: grpc.aio.ServicerContext,
    ) -> module_pb2.CallToolReply:
        tool = self._tools.get(request.name)
        if tool is None:
            return _error(f"Error: unknown tool '{request.name}'")
        try:
            args = json.loads(request.arguments_json or "{}")
        except json.JSONDecodeError:
            return _error(f"Error: arguments for '{request.name}' are not valid JSON")
        if not isinstance(args, dict):
            return _error(f"Error: arguments for '{request.name}' must be a JSON object")
        problem = validate_args(tool.input_schema, args)
        if problem is not None:
            return _error(f"Error: invalid arguments for '{request.name}': {problem}")
        logger.info("CallTool %s by agent=%s", request.name, request.agent_id or "<none>")
        try:
            value = await tool.handler(**args)
        except TypeError as e:
            # Still possible when the schema is silent (no properties declared).
            return _error(f"Error: bad arguments for '{request.name}': {e}")
        except Exception as e:
            logger.exception("Tool '%s' failed", request.name)
            return _error(f"Error: tool '{request.name}' failed: {e}")
        result = to_result(value)
        return module_pb2.CallToolReply(content=result.text, is_error=result.is_error)
