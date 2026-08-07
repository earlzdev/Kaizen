# =============================================================================
# Unit tests — infra/modkit (ToolResult, validate_args, the shared servicer)
# =============================================================================
# WHAT: the shared module runtime: the legacy str→ToolResult shim, schema
#       validation of tool arguments, and the one gRPC servicer that replaced
#       three byte-identical copies (Step 5 of ARCHITECTURE_REVIEW.md).
# WHY: this is now THE dispatch path for every module tool — a regression here
#       breaks mentor, tracker and tools/ at once.
# HOW: the servicer is exercised directly with real module_pb2 messages
#       (context is unused by the implementation, so None is passed).
# =============================================================================

import json

from infra.modkit import ModuleServicer, ToolDef, ToolResult, to_result, validate_args
from infra.proto.gen import module_pb2

# ----- to_result (the legacy shim) ----------------------------------------


def test_to_result_passes_toolresult_through():
    r = ToolResult("hi", is_error=True)
    assert to_result(r) is r


def test_to_result_applies_legacy_prefix_rule():
    assert to_result("all good") == ToolResult("all good", False)
    assert to_result("Error: nope") == ToolResult("Error: nope", True)


# ----- validate_args -------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "days": {"type": "integer"},
        "detailed": {"type": "boolean"},
    },
    "required": ["city"],
}


def test_validate_ok():
    assert validate_args(SCHEMA, {"city": "Уфа", "days": 3}) is None


def test_validate_missing_required():
    problem = validate_args(SCHEMA, {"days": 3})
    assert "missing required" in problem and "city" in problem


def test_validate_unknown_argument_lists_expected():
    problem = validate_args(SCHEMA, {"city": "Уфа", "cty": "x"})
    assert "unknown argument" in problem and "cty" in problem and "days" in problem


def test_validate_wrong_type():
    assert "must be a integer" in validate_args(SCHEMA, {"city": "Уфа", "days": "3"})
    assert "must be a string" in validate_args(SCHEMA, {"city": 5})


def test_validate_bool_is_not_an_integer():
    assert "boolean" in validate_args(SCHEMA, {"city": "Уфа", "days": True})


def test_validate_number_accepts_int_and_float():
    schema = {"type": "object", "properties": {"x": {"type": "number"}}}
    assert validate_args(schema, {"x": 1}) is None
    assert validate_args(schema, {"x": 1.5}) is None


def test_validate_empty_properties_rejects_any_argument():
    schema = {"type": "object", "properties": {}}
    assert "unknown argument" in validate_args(schema, {"anything": 1})
    assert validate_args(schema, {}) is None


def test_validate_silent_schema_blocks_nothing():
    # No properties key / not an object schema: nothing to check.
    assert validate_args({"type": "object"}, {"whatever": 1}) is None
    assert validate_args({}, {"whatever": 1}) is None


# ----- ModuleServicer ------------------------------------------------------


def _servicer(handler=None, schema=None) -> ModuleServicer:
    async def default_handler(city: str, days: int = 3):
        return f"{city}/{days}"

    return ModuleServicer(
        "testmod",
        [ToolDef("weather", "desc", schema or SCHEMA, handler or default_handler)],
    )


def _call(name: str, args: dict | None = None, raw: str | None = None):
    return module_pb2.CallToolRequest(
        name=name,
        arguments_json=raw if raw is not None else json.dumps(args or {}),
        agent_id="kaya",
    )


async def test_servicer_registers_specs():
    reply = await _servicer().RegisterTools(
        module_pb2.RegisterToolsRequest(brain_version="1.0.0"), None
    )
    assert reply.module == "testmod"
    assert [t.name for t in reply.tools] == ["weather"]
    assert json.loads(reply.tools[0].input_schema_json) == SCHEMA


async def test_servicer_dispatches_and_wraps_str():
    reply = await _servicer().CallTool(_call("weather", {"city": "Уфа"}), None)
    assert (reply.content, reply.is_error) == ("Уфа/3", False)


async def test_servicer_unknown_tool():
    reply = await _servicer().CallTool(_call("nope", {}), None)
    assert reply.is_error and "unknown tool" in reply.content


async def test_servicer_rejects_bad_json_and_non_object():
    reply = await _servicer().CallTool(_call("weather", raw="{not json"), None)
    assert reply.is_error and "not valid JSON" in reply.content
    reply = await _servicer().CallTool(_call("weather", raw="[1,2]"), None)
    assert reply.is_error and "JSON object" in reply.content


async def test_servicer_validates_before_dispatch():
    reply = await _servicer().CallTool(_call("weather", {"city": "Уфа", "cty": 1}), None)
    assert reply.is_error and "invalid arguments" in reply.content


async def test_servicer_handler_toolresult_is_passed_through():
    async def handler():
        return ToolResult("Error: looks like an error but IS the answer", False)

    servicer = _servicer(handler=handler, schema={"type": "object", "properties": {}})
    reply = await servicer.CallTool(_call("weather", {}), None)
    # A ToolResult says what it means — the "Error:" prefix does NOT flip it.
    assert (reply.is_error, reply.content) == (False, "Error: looks like an error but IS the answer")


async def test_servicer_handler_exception_is_error_reply():
    async def handler(city: str, days: int = 3):
        raise RuntimeError("boom")

    reply = await _servicer(handler=handler).CallTool(_call("weather", {"city": "x"}), None)
    assert reply.is_error and "failed" in reply.content and "boom" in reply.content


def test_servicer_duplicate_name_keeps_first():
    async def h1():
        return "one"

    async def h2():
        return "two"

    servicer = ModuleServicer(
        "m",
        [ToolDef("t", "", {"type": "object"}, h1), ToolDef("t", "", {"type": "object"}, h2)],
    )
    assert servicer.tools["t"].handler is h1
