# =============================================================================
# Unit tests — agents/core/llm.py (prompt-cache markers + usage mapping)
# =============================================================================
# WHAT: how the API backend lays out the request for Anthropic prompt caching —
#       where the cache_control breakpoints land — and how a response's `usage`
#       becomes a TurnUsage.
# WHY: a regression here is INVISIBLE. Drop a marker and every reply still comes
#       back correct, just at full input price on a ~20 KB prefix, every turn,
#       forever. There is no failing assertion in production to catch it, so it
#       has to be caught here.
# HOW: pure calls on the module's helper functions and a hand-built response.
# =============================================================================

from agents.core.llm import _cache_last_message, _system_blocks, _to_usage


def _marked(block) -> bool:
    return isinstance(block, dict) and "cache_control" in block


# ---------------------------------------------------------------------------
# System blocks: the breakpoint sits between the stable head and the runtime
# ---------------------------------------------------------------------------


def test_stable_head_is_cached_and_runtime_is_not():
    blocks = _system_blocks("SOUL+PROTOCOL", "## Runtime context\nnow: 12:00")
    assert len(blocks) == 2
    assert blocks[0]["text"] == "SOUL+PROTOCOL"
    assert _marked(blocks[0])       # tools + head are cached behind this marker
    assert not _marked(blocks[1])   # the volatile half must stay outside it


def test_head_alone_is_still_cached():
    blocks = _system_blocks("SOUL", "")
    assert len(blocks) == 1
    assert _marked(blocks[0])


# ---------------------------------------------------------------------------
# Conversation breakpoint: makes the NEXT tool-loop iteration cheap
# ---------------------------------------------------------------------------


def test_last_string_message_becomes_a_marked_block():
    out = _cache_last_message([{"role": "user", "content": "hi"}])
    block = out[0]["content"][0]
    assert block["text"] == "hi" and _marked(block)


def test_only_the_last_message_is_marked():
    out = _cache_last_message(
        [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    )
    assert out[0]["content"] == "a"   # untouched
    assert _marked(out[1]["content"][0])


def test_last_block_of_a_tool_result_message_is_marked():
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": "x"},
            {"type": "tool_result", "tool_use_id": "2", "content": "y"},
        ]}
    ]
    out = _cache_last_message(messages)
    blocks = out[0]["content"]
    assert not _marked(blocks[0]) and _marked(blocks[1])


def test_the_callers_messages_are_never_mutated():
    """The loop reuses its msgs list across iterations and the history window is
    shared — marking in place would stack a breakpoint onto every turn (there
    are only four) and corrupt what gets persisted."""
    original = [{"role": "user", "content": "hi"}]
    _cache_last_message(original)
    assert original == [{"role": "user", "content": "hi"}]

    nested = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    _cache_last_message(nested)
    assert nested[0]["content"] == [{"type": "text", "text": "hi"}]


def test_unmarkable_content_is_left_alone():
    # Empty string content (the API rejects it anyway) and provider objects
    # echoed back verbatim: skip rather than guess at their shape.
    assert _cache_last_message([{"role": "user", "content": ""}])[0]["content"] == ""
    assert _cache_last_message([]) == []

    class ProviderBlock:  # the SDK's own object, not a dict
        pass

    blocks = [ProviderBlock()]
    out = _cache_last_message([{"role": "assistant", "content": blocks}])
    assert out[0]["content"] is blocks


# ---------------------------------------------------------------------------
# Usage mapping
# ---------------------------------------------------------------------------


class _Usage:
    input_tokens = 11
    output_tokens = 22
    cache_read_input_tokens = 3300
    cache_creation_input_tokens = 440


class _Response:
    usage = _Usage()


def test_usage_is_mapped_including_the_cache_counters():
    usage = _to_usage(_Response())
    assert (usage.input_tokens, usage.output_tokens) == (11, 22)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (3300, 440)
    assert usage.cost_usd is None  # the API bills elsewhere; only the CLI knows


def test_missing_usage_is_none_not_an_error():
    assert _to_usage(object()) is None


def test_unexpected_field_types_degrade_to_zero():
    class Weird:
        usage = type("U", (), {"input_tokens": None})()

    assert _to_usage(Weird()).input_tokens == 0
