# =============================================================================
# Tool definition + result — infra/modkit/tooldef.py
# =============================================================================
# WHAT: ToolDef (name + LLM-facing description + input JSON schema + async
#       handler), ToolResult (text + is_error — the structured outcome),
#       to_result (the legacy shim for handlers that still return a bare str),
#       and validate_args (arguments checked against the schema BEFORE the
#       `**args` splat reaches a handler).
#
# WHY ToolResult instead of the "Error:" prefix: whether a call failed used to
#       be decided by text.startswith("Error:") in four different places — a
#       tool whose legitimate output began with "Error:" was misreported, and a
#       tool that forgot the prefix reported success on failure. The gRPC
#       contract always carried a real is_error bool; now the Python side does
#       too. Handlers may keep returning str during migration — to_result()
#       applies the old prefix rule in exactly ONE place.
#
# WHY validate_args is hand-rolled (no jsonschema dependency): every schema in
#       this repo is a flat "type: object" with primitive-typed properties; the
#       subset below covers them fully. What it must catch is the model
#       inventing an argument name or type — previously a TypeError from the
#       splat, surfaced as an unhelpful generic failure.
#
# HOW: `problem = validate_args(tool.input_schema, args)` -> None or a message;
#      `result = to_result(await tool.handler(**args))`.
# =============================================================================

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# str returns are legacy (the "Error:" prefix rule applies via to_result);
# new handlers return ToolResult and say what they mean.
ToolHandler = Callable[..., Awaitable["str | ToolResult"]]


@dataclass(frozen=True)
class ToolResult:
    """One tool call's outcome: the text the agent's model reads + whether it
    is an error (the model then reacts instead of trusting the text)."""

    text: str
    is_error: bool = False


@dataclass
class ToolDef:
    """One tool exposed over the Module contract / Brain's registry."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    # OPTIONAL "how to call me well", with a concrete example — as opposed to
    # `description` ("what I am / when to call me"). The agent renders these
    # into its system prompt, so usage guidance ships WITH the tool instead of
    # being copied into every agent's soul. Leave it empty unless the tool's
    # arguments are genuinely easy to get wrong: the rendered block travels in
    # EVERY prompt, so 31 verbose tools would drown the model in boilerplate.
    usage: str = ""


def to_result(value: "str | ToolResult") -> ToolResult:
    """Normalize a handler's return into a ToolResult.

    The legacy shim lives HERE and nowhere else: a bare str is an error iff it
    starts with "Error:" — the old convention, applied in one owned place
    instead of four scattered .startswith() checks."""
    if isinstance(value, ToolResult):
        return value
    return ToolResult(text=value, is_error=value.startswith("Error:"))


# JSON-schema primitive -> accepted Python types. bool is deliberately NOT an
# integer/number here (Python's bool subclasses int; JSON's doesn't).
_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    """Check `args` against a flat object schema. Returns a human/model-readable
    problem description, or None when the args are acceptable.

    Deliberately permissive where the schema is silent: no "type": "object", or
    no "properties" key at all -> nothing to check (the TypeError fallback in
    the dispatcher still guards the splat). With a declared properties map,
    unknown argument names are rejected — that is exactly the failure mode of a
    model hallucinating a parameter."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    props = schema.get("properties")
    required = schema.get("required") or []

    missing = [k for k in required if k not in args]
    if missing:
        return f"missing required argument(s): {', '.join(sorted(missing))}"

    if not isinstance(props, dict):
        return None
    unknown = [k for k in args if k not in props]
    if unknown:
        expected = ", ".join(sorted(props)) or "(no arguments)"
        return (
            f"unknown argument(s): {', '.join(sorted(unknown))}; "
            f"expected: {expected}"
        )
    for key, value in args.items():
        declared = props.get(key)
        expected_type = declared.get("type") if isinstance(declared, dict) else None
        accepted = _TYPES.get(expected_type or "")
        if accepted is None:
            continue  # untyped/unfamiliar declaration — don't block
        if isinstance(value, bool) and expected_type in ("integer", "number"):
            return f"argument '{key}' must be a {expected_type}, got a boolean"
        if not isinstance(value, accepted):
            return f"argument '{key}' must be a {expected_type}"
    return None
