# =============================================================================
# Unit tests — agents/core/cli.py (_parse_event: the stream-json wire parser)
# =============================================================================
# WHAT: parsing of the claude CLI's stream-json events — the final 'result',
#       tool_use extraction with the mcp__server__ prefix strip, junk lines.
# WHY: this parser is the only bridge between the CLI backend and the Agent's
#       gate logic (tools_used decides whether a turn gets fact-checked).
# HOW: pure static-method calls on hand-built JSON lines.
# =============================================================================

import json

from agents.core.cli import ClaudeCliRunner

parse = ClaudeCliRunner._parse_event


def _line(obj) -> bytes:
    return json.dumps(obj).encode()


def test_result_event_yields_final_text():
    final, statuses = parse(_line({"type": "result", "result": "the answer"}), [])
    assert final == "the answer"
    assert statuses == []


def test_result_event_with_null_result_is_empty_string_not_none():
    # "" (not None) so the caller can tell "empty result event" from "no event".
    final, _ = parse(_line({"type": "result", "result": None}), [])
    assert final == ""


def test_tool_use_strips_mcp_prefix_and_records_usage():
    tools_used = []
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": "mcp__brain__find_online", "input": {"query": "e-ink"}},
                {"type": "text", "text": "thinking..."},
            ]
        },
    }
    final, statuses = parse(_line(event), tools_used)
    assert final is None
    assert tools_used == ["find_online"]
    assert statuses == [("find_online", "e-ink")]


def test_builtin_tool_name_passes_through():
    tools_used = []
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x"}}]},
    }
    _, statuses = parse(_line(event), tools_used)
    assert tools_used == ["Read"]
    assert statuses == [("Read", "/tmp/x")]


def test_invalid_json_line_is_ignored():
    assert parse(b"not json at all\n", []) == (None, [])


def test_unknown_event_type_is_ignored():
    assert parse(_line({"type": "system", "subtype": "init"}), []) == (None, [])


# ---------------------------------------------------------------------------
# Session id + usage capture (the `meta` out-parameter)
# ---------------------------------------------------------------------------


def test_session_id_is_captured_from_the_init_event():
    # Captured from ANY event, not just the result one, so a turn that dies
    # halfway still leaves a session that a retry could resume.
    meta = {}
    parse(_line({"type": "system", "subtype": "init", "session_id": "s-42"}), [], meta)
    assert meta["session_id"] == "s-42"


def test_result_event_yields_usage_and_cost():
    meta = {}
    event = {
        "type": "result",
        "result": "done",
        "session_id": "s-1",
        "total_cost_usd": 0.0731,
        "usage": {
            "input_tokens": 12,
            "output_tokens": 34,
            "cache_read_input_tokens": 5600,
            "cache_creation_input_tokens": 700,
        },
    }
    final, _ = parse(_line(event), [], meta)
    assert final == "done"
    assert meta["session_id"] == "s-1"
    usage = meta["usage"]
    assert (usage.input_tokens, usage.output_tokens) == (12, 34)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (5600, 700)
    assert usage.cost_usd == 0.0731


def test_result_event_without_usage_does_not_explode():
    # Telemetry is never worth a failed turn — a CLI that renames these fields
    # must cost us the numbers, not the reply.
    meta = {}
    final, _ = parse(_line({"type": "result", "result": "done"}), [], meta)
    assert final == "done"
    assert meta["usage"].input_tokens == 0
    assert meta["usage"].cost_usd is None


def test_meta_is_optional():
    # The parser is called without meta in plenty of places; it must not care.
    assert parse(_line({"type": "result", "result": "x"}), []) == ("x", [])


# ---------------------------------------------------------------------------
# Prompt assembly (what actually reaches the model, and where)
# ---------------------------------------------------------------------------

MESSAGES = [
    {"role": "user", "content": "привет"},
    {"role": "assistant", "content": "привет!"},
    {"role": "user", "content": "как дела?"},
]


def test_transcript_puts_runtime_in_the_body_not_the_system_prompt():
    """The cache contract: volatile context rides in the prompt body, so
    --append-system-prompt stays byte-identical between turns."""
    body = ClaudeCliRunner._transcript(MESSAGES, "## Runtime context\nnow: 12:00")
    assert body.startswith("## Runtime context")
    assert "User: как дела?" in body


def test_transcript_without_runtime_is_unchanged():
    body = ClaudeCliRunner._transcript(MESSAGES)
    assert body.startswith("User: привет")
    assert "---" not in body


def test_resumed_turn_sends_only_the_newest_message():
    """A resumed session already holds the history — replaying it would defeat
    the entire point of resuming."""
    assert ClaudeCliRunner._last_message(MESSAGES) == "как дела?"
    assert ClaudeCliRunner._last_message([]) == ""
