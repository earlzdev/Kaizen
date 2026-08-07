# =============================================================================
# Unit tests — brain/delivery.py (push failure modes, incl. the timeout bug)
# =============================================================================
# WHAT: DeliveryClient.push against a real local aiohttp server: 2xx, 4xx/5xx,
#       and — the regression this file exists for — a TOTAL TIMEOUT.
# WHY: aiohttp's total-timeout raises TimeoutError, which is NOT a ClientError;
#       before the fix it escaped push(), aborted the rest of the sweep, and was
#       swallowed by the sweeper's loop guard (ARCHITECTURE_REVIEW.md §2.2-1).
# HOW: aiohttp.test_utils.TestServer on an ephemeral port; no Brain, no DB.
# =============================================================================

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from brain.delivery import DeliveryClient

EVENT = {"kind": "reminder", "text": "⏰ Напоминание: тест"}


async def _server(handler) -> TestServer:
    app = web.Application()
    app.router.add_post("/deliver", handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_push_ok_on_2xx():
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer tok"
        assert await request.json() == EVENT
        return web.json_response({"ok": True})

    server = await _server(handler)
    try:
        assert await DeliveryClient("tok").push(str(server.make_url("/deliver")), EVENT) is True
    finally:
        await server.close()


async def test_push_ok_on_202_accepted():
    """A wake-up push is answered 202 (the turn runs in the background — Brain
    only gives a push 10s). If that stopped counting as success the sweeper
    would never mark the reminder delivered and would re-wake the agent every
    backoff round, messaging the owner each time."""

    async def handler(request):
        return web.json_response({"ok": True, "started": True}, status=202)

    server = await _server(handler)
    try:
        assert await DeliveryClient("tok").push(str(server.make_url("/deliver")), EVENT) is True
    finally:
        await server.close()


async def test_push_false_on_http_error():
    async def handler(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    server = await _server(handler)
    try:
        assert await DeliveryClient("tok").push(str(server.make_url("/deliver")), EVENT) is False
    finally:
        await server.close()


async def test_push_false_on_timeout_not_raised():
    """The regression test: a hung receiver must yield False, not an exception."""

    async def handler(request):
        await asyncio.sleep(1.0)  # far beyond the client timeout below
        return web.json_response({"ok": True})

    server = await _server(handler)
    try:
        client = DeliveryClient("tok", timeout=0.1)
        assert await client.push(str(server.make_url("/deliver")), EVENT) is False
    finally:
        await server.close()


async def test_push_false_on_unreachable_addr():
    # A port nothing listens on — connection refused is a ClientError.
    assert await DeliveryClient("tok", timeout=1.0).push("http://127.0.0.1:1/deliver", EVENT) is False
