# =============================================================================
# Unit tests — brain/server.py (POST /admin/modules/refresh)
# =============================================================================
# WHAT: the admin re-discovery endpoint: auth gating, the no-modules case, and
#       the pass-through of the router's per-module summary.
# WHY: this endpoint is Step 2's manual half — it must be admin-only (it can
#       mutate the registry) and must not 500 when BRAIN_MODULES is empty.
# HOW: BrainServer over a real ToolRegistry and a fake router, served by
#       aiohttp's TestServer/TestClient; no DB is touched (auth fails first or
#       the fake router answers).
# =============================================================================

from aiohttp.test_utils import TestClient, TestServer

from brain.access import AccessControl
from brain.agents import AgentStore
from brain.registry import ToolRegistry
from brain.server import BrainServer


class FakeRouter:
    def __init__(self, summary):
        self._summary = summary
        self.refreshed = 0

    async def refresh(self, registry):
        self.refreshed += 1
        return self._summary


def _server(modules_router) -> BrainServer:
    return BrainServer(
        registry=ToolRegistry(),
        store=AgentStore(),
        access=AccessControl(),
        admin_token="admin-tok",
        modules_router=modules_router,
    )


async def _client(server: BrainServer) -> TestClient:
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    return client


async def test_refresh_requires_admin_token():
    client = await _client(_server(FakeRouter({})))
    try:
        resp = await client.post("/admin/modules/refresh")
        assert resp.status == 401
        resp = await client.post(
            "/admin/modules/refresh", headers={"Authorization": "Bearer wrong"}
        )
        assert resp.status == 401
    finally:
        await client.close()


async def test_refresh_returns_router_summary():
    router = FakeRouter({"mentor": {"status": "ok", "tools_added": 2}})
    client = await _client(_server(router))
    try:
        resp = await client.post(
            "/admin/modules/refresh", headers={"Authorization": "Bearer admin-tok"}
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["modules"] == {"mentor": {"status": "ok", "tools_added": 2}}
        assert body["tools_total"] == 0  # the fake registered nothing
        assert router.refreshed == 1
    finally:
        await client.close()


async def test_refresh_without_configured_modules_is_404():
    client = await _client(_server(None))
    try:
        resp = await client.post(
            "/admin/modules/refresh", headers={"Authorization": "Bearer admin-tok"}
        )
        assert resp.status == 404
    finally:
        await client.close()
