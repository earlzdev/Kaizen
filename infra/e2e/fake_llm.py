"""WHAT: a fake Anthropic Messages API server — one route, `POST /v1/messages`,
returning scripted responses instead of running a real model.
WHY: `agents/core/llm.py`'s AnthropicClient constructs `AsyncAnthropic(api_key=…)`
with no explicit `base_url`, so the SDK's own `ANTHROPIC_BASE_URL` env var
handling points it here unmodified — the e2e `flow` tier drives Кая's REAL
tool loop (dedup, history, delivery all still run) against a scripted,
deterministic model instead of a real (slow, non-deterministic, billed) one.
See docs/e2e/README.md §8.
HOW: a test POSTs a list of scripted turns to `/_script` before driving a
conversation; each `/v1/messages` call pops the next one (FIFO) or 400s if the
queue is empty — an unscripted call is a test bug, not something to paper
over with a silent default. `/_reset` clears the queue and call log between
scenarios; `GET /_calls` returns every request received, for asserting on
what the real tool loop actually sent (tool schemas, message history), not
just on what it got back.
"""
import uuid

from aiohttp import web

_queue: list[dict] = []
_calls: list[dict] = []


def _fake_response(turn: dict) -> dict:
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": turn.get("model", "e2e-fake-model"),
        "content": turn["content"],
        "stop_reason": turn.get("stop_reason", "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


async def handle_messages(request: web.Request) -> web.Response:
    body = await request.json()
    _calls.append(body)
    if not _queue:
        return web.json_response(
            {"type": "error", "error": {"type": "invalid_request_error",
             "message": "fake-llm: no scripted turn queued — POST /_script first"}},
            status=400,
        )
    return web.json_response(_fake_response(_queue.pop(0)))


async def handle_script(request: web.Request) -> web.Response:
    """Body: a list of turns, each `{"content": [<Anthropic content blocks>],
    "stop_reason": "tool_use" | "end_turn", ...}`. Appended to the FIFO queue —
    call `/_reset` first for a clean slate."""
    turns = await request.json()
    _queue.extend(turns)
    return web.json_response({"queued": len(_queue)})


async def handle_reset(request: web.Request) -> web.Response:
    _queue.clear()
    _calls.clear()
    return web.json_response({"ok": True})


async def handle_calls(request: web.Request) -> web.Response:
    return web.json_response(_calls)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "queued": len(_queue)})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/v1/messages", handle_messages)
    app.router.add_post("/_script", handle_script)
    app.router.add_post("/_reset", handle_reset)
    app.router.add_get("/_calls", handle_calls)
    app.router.add_get("/health", handle_health)
    return app


if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=8790)
