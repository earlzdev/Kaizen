# =============================================================================
# Unit tests — brain/registry.py (structured execute + validation)
# =============================================================================
# WHAT: ToolRegistry.execute after Step 5: returns ToolResult, validates
#       arguments against the declared schema before the ** splat, keeps the
#       legacy str shim for Brain's built-in handlers.
# WHY: this is the single dispatch point for EVERY tool an agent calls through
#       Brain — built-ins and module proxies alike.
# HOW: pure in-memory registry, no DB/gRPC.
# =============================================================================

import pytest

from brain.registry import Tool, ToolRegistry
from infra.modkit import ToolResult

SCHEMA = {
    "type": "object",
    "properties": {"fact": {"type": "string"}},
    "required": ["fact"],
}


def _registry(handler) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("remember", "", SCHEMA, handler, module=None))
    return registry


async def test_execute_wraps_legacy_str_success_and_error():
    async def ok(fact: str):
        return f"Remembered: {fact}"

    async def err(fact: str):
        return "Error: storage down"

    assert await _registry(ok).execute("remember", {"fact": "x"}) == ToolResult(
        "Remembered: x", False
    )
    assert (await _registry(err).execute("remember", {"fact": "x"})).is_error is True


async def test_execute_passes_toolresult_through():
    async def handler(fact: str):
        return ToolResult("Error: this is data, not a failure", is_error=False)

    result = await _registry(handler).execute("remember", {"fact": "x"})
    assert result.is_error is False  # no prefix sniffing on structured results


async def test_execute_validates_arguments():
    async def handler(fact: str):
        return "ok"

    # A typo'd key means the required one is missing — that is the first,
    # most useful thing to tell the model.
    result = await _registry(handler).execute("remember", {"fct": "typo"})
    assert result.is_error
    assert "invalid arguments" in result.text and "missing required" in result.text

    # With the required key present, the invented extra key is called out.
    result = await _registry(handler).execute("remember", {"fact": "x", "extra": 1})
    assert result.is_error and "unknown argument" in result.text and "extra" in result.text


async def test_execute_unknown_tool():
    result = await ToolRegistry().execute("nope", {})
    assert result.is_error and "unknown tool" in result.text


async def test_execute_handler_exception_becomes_error_result():
    async def handler(fact: str):
        raise RuntimeError("boom")

    result = await _registry(handler).execute("remember", {"fact": "x"})
    assert result.is_error and "failed" in result.text


def test_duplicate_registration_raises():
    async def handler():
        return "x"

    registry = ToolRegistry()
    registry.register(Tool("t", "", {}, handler))
    with pytest.raises(ValueError):
        registry.register(Tool("t", "", {}, handler))
