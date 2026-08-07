# =============================================================================
# Brain MCP server — brain/server.py
# =============================================================================
# WHAT: The MCP front door of Brain (the "MCP спереди" half). Ported from the v1
#       app/context/server.py, with two Phase-2 additions:
#         1. it runs on Brain's own DB (agents/facts/... isolated from v1);
#         2. it ENFORCES the per-agent access-list — filtering tools/list and
#            gating tools/call — which the v1 Context Service did not.
#
# WHAT it serves:
#   POST /mcp            — the MCP endpoint (initialize/ping/tools/list/tools/call)
#   POST /admin/agents   — mint an agent (admin token); GET lists them
#   POST /event          — a MODULE asks Brain to tell an agent something
#                          (module-event token). The one inversion of the usual
#                          agent → Brain → module direction; see _module_event.
#   GET  /health         — liveness
#
# WHY the identity split (actor vs subject):
#   subject = whose data — the single owner, implicit (Brain is single-tenant,
#             memory carries no user_id).
#   actor   = who is acting — resolved from the bearer token per request and
#             published on the provenance ContextVar so every write is stamped.
#
# WHY access-list enforcement lives HERE (not in the tools): Brain is the trust
#   boundary. It authenticates the agent, then decides — BEFORE dispatch —
#   whether that agent may see/call the tool. A denied call returns an isError
#   result (not a silent success and not a crash), so the agent's model can
#   react. allow-by-default: an agent with no rules sees every tool.
#
# SECURITY: binds 0.0.0.0 (external agents must reach it), which is exactly why
#   every /mcp and /admin route requires a token. No token, no tools.
# =============================================================================

import logging
import secrets
from typing import Any

import aiohttp
from aiohttp import web

from pydantic import BaseModel, ValidationError

from infra.modkit import DeliveryEvent

from brain.access import AccessControl
from brain.agents import AgentStore
from brain.api_models import (
    AddAccessRuleRequest,
    CreateAgentRequest,
    EnrollRequest,
    EnrollStatusRequest,
    ModuleEventRequest,
    SetDeliveryRequest,
)
from brain.memory import MemoryStore
from brain.panel import PANEL_HTML
from brain.provenance import current_actor_id, current_actor_slug
from brain.registry import ToolRegistry

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def _result(req_id: Any, result: dict) -> web.Response:
    return web.json_response({"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result})


def _error(req_id: Any, code: int, message: str) -> web.Response:
    return web.json_response(
        {"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": {"code": code, "message": message}}
    )


def _bearer(request: web.Request) -> str:
    """Extract the bearer token from the Authorization header ('' if absent)."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return ""


async def parse_body[M: BaseModel](
    request: web.Request, model: type[M]
) -> tuple[M | None, web.Response | None]:
    """Read + validate a JSON body against a pydantic model (Step 7: the
    request contract lives in brain/api_models.py, not in .get() chains).
    Returns (model, None) on success, (None, ready 400 response) on any
    malformed input — one uniform failure shape for every route."""
    try:
        data = await request.json()
    except Exception:
        return None, web.json_response({"error": "body must be JSON"}, status=400)
    try:
        return model.model_validate(data), None
    except ValidationError as e:
        first = e.errors()[0]
        field = ".".join(str(p) for p in first["loc"]) or "body"
        return None, web.json_response(
            {"error": f"invalid body: {field}: {first['msg']}"}, status=400
        )


def _int_or_none(value: str | None) -> int | None:
    """Parse an int from a path/query string, or None if absent/malformed."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class BrainServer:
    """Serves Brain's registry over authenticated, access-controlled MCP."""

    def __init__(
        self,
        registry: ToolRegistry,
        store: AgentStore,
        access: AccessControl,
        admin_token: str,
        memory: MemoryStore | None = None,
        backup_url: str = "",
        backup_token: str = "",
        enroll=None,
        enroll_token: str = "",
        modules_router=None,
        delivery=None,
        module_event_token: str = "",
        default_delivery_slug: str = "",
    ) -> None:
        self._registry = registry
        self._store = store
        self._access = access
        self._admin_token = admin_token
        self._memory = memory
        self._backup_url = backup_url.rstrip("/")
        self._backup_token = backup_token
        self._enroll = enroll
        self._enroll_token = enroll_token
        # ModuleRouter (or None when BRAIN_MODULES is empty) — powers the
        # admin "re-discover module tools now" endpoint.
        self._modules_router = modules_router
        # The outbound push client + its gate, for POST /event (see below).
        self._delivery = delivery
        self._module_event_token = module_event_token
        self._default_delivery_slug = default_delivery_slug

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/mcp", self._handle_mcp)
        app.router.add_post("/mcp/", self._handle_mcp)
        app.router.add_get("/mcp", self._no_stream)
        app.router.add_post("/admin/agents", self._create_agent)
        app.router.add_get("/admin/agents", self._list_agents)
        app.router.add_post("/agent/delivery", self._set_delivery)
        app.router.add_post("/event", self._module_event)
        # --- Admin panel (Phase 8): UI + its data endpoints ---
        app.router.add_get("/admin/panel", self._panel)
        app.router.add_get("/admin/tools", self._admin_tools)
        app.router.add_get("/admin/access", self._admin_list_access)
        app.router.add_post("/admin/access", self._admin_add_access)
        app.router.add_delete("/admin/access/{id}", self._admin_delete_access)
        app.router.add_get("/admin/memory", self._admin_memory)
        app.router.add_get("/admin/provenance", self._admin_provenance)
        app.router.add_get("/admin/reminders", self._admin_reminders)
        app.router.add_delete("/admin/reminders/{id}", self._admin_delete_reminder)
        app.router.add_get("/admin/profile", self._admin_profile)
        app.router.add_get("/admin/backups", self._admin_backups_list)
        app.router.add_post("/admin/backups", self._admin_backups_create)
        app.router.add_post("/admin/modules/refresh", self._admin_modules_refresh)
        # --- enrollment (device pairing) ---
        app.router.add_post("/enroll", self._enroll_request)
        # POST (was GET): the secret travels in the body, not the query string —
        # query strings land in proxy/access logs (Step 4 of ARCHITECTURE_REVIEW.md).
        app.router.add_post("/enroll/status", self._enroll_status)
        app.router.add_get("/admin/enrollments", self._admin_enrollments)
        app.router.add_post("/admin/enrollments/{id}/approve", self._admin_enroll_approve)
        app.router.add_post("/admin/enrollments/{id}/reject", self._admin_enroll_reject)
        app.router.add_get("/health", self._health)
        return app

    # ----- health -----

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "tools": len(self._registry.all())})

    async def _no_stream(self, request: web.Request) -> web.Response:
        return web.Response(status=405, text="This MCP server does not support SSE streaming")

    # ----- admin (mint / list agents) -----

    def _is_admin(self, request: web.Request) -> bool:
        # compare_digest: a plain == leaks how many leading characters matched
        # via response timing — irrelevant for a wrong-length guess, decisive
        # for an attacker iterating character by character.
        return bool(self._admin_token) and secrets.compare_digest(
            _bearer(request), self._admin_token
        )

    async def _create_agent(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body, err = await parse_body(request, CreateAgentRequest)
        if err is not None:
            return err
        try:
            agent, token = await self._store.create_agent(
                body.slug, delivery_addr=body.delivery_addr or None
            )
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=409)
        # The token is shown ONCE — the caller must save it now.
        return web.json_response(
            {"id": agent.id, "slug": agent.slug, "token": token}, status=201
        )

    async def _list_agents(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        agents = await self._store.list_agents()
        return web.json_response(
            {"agents": [{"id": a.id, "slug": a.slug, "delivery_addr": a.delivery_addr} for a in agents]}
        )

    async def _set_delivery(self, request: web.Request) -> web.Response:
        """An agent registers where Brain can PUSH events to it (Phase 6). Auth
        is the agent's OWN bearer token — an agent may only set its own address."""
        agent = await self._store.authenticate(_bearer(request))
        if agent is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        body, err = await parse_body(request, SetDeliveryRequest)
        if err is not None:
            return err
        await self._store.set_delivery_addr(agent.id, body.delivery_addr)
        return web.json_response(
            {"ok": True, "slug": agent.slug, "delivery_addr": body.delivery_addr}
        )

    async def _module_event(self, request: web.Request) -> web.Response:
        """A MODULE asks Brain to tell an agent something (tracker v2, Step 5).

        WHY this route exists at all — it is the one inversion in the system.
        Everywhere else the flow is agent → Brain → module, and Brain dials
        modules. But the tracker Hub genuinely originates news: a project's PR
        is ready, or one of its agents needs the owner to decide something. That
        has to reach Кая, and the alternative — letting the module push straight
        to the agent — would mean every module learning agents' addresses and
        the delivery token. So the module says WHAT happened, and Brain, which
        already owns the agent registry and the push client, decides WHO hears
        it and HOW to reach them.

        WHY its own token and not the admin token: this grants exactly "send a
        message to an agent". A module that leaked its event token cannot mint
        agents, read memory, or trigger a backup.
        """
        # Fail closed: an unset token rejects everything, so a half-configured
        # deployment is silent rather than open.
        if not self._module_event_token or not secrets.compare_digest(
            _bearer(request), self._module_event_token
        ):
            return web.json_response({"error": "unauthorized"}, status=401)
        if self._delivery is None:
            return web.json_response({"error": "delivery is not configured"}, status=503)

        body, err = await parse_body(request, ModuleEventRequest)
        if err is not None:
            return err

        slug = body.agent or self._default_delivery_slug
        if not slug:
            return web.json_response({"error": "no target agent configured"}, status=503)
        agent = await self._store.get_by_slug(slug)
        if agent is None or not agent.delivery_addr:
            # 503, not 404: the module did nothing wrong and the event is not
            # malformed — the target agent simply isn't reachable yet (it may
            # not have enrolled). The caller logs and moves on.
            logger.warning(
                "Module event '%s' has no reachable target (agent '%s')", body.kind, slug
            )
            return web.json_response({"error": f"agent '{slug}' has no address"}, status=503)

        # Every module event reaches the agent as ONE delivery kind. The
        # module's own kind stays in the log: agents shouldn't grow a handler
        # per module event, and the text already says what happened.
        event = DeliveryEvent(kind="tracker", text=body.text)
        ok = await self._delivery.push(agent.delivery_addr, event.model_dump())
        logger.info(
            "Module event '%s' -> %s: %s", body.kind, slug, "delivered" if ok else "FAILED"
        )
        if not ok:
            return web.json_response({"error": "delivery failed"}, status=502)
        return web.json_response({"ok": True, "agent": slug})

    # ----- admin panel + its data endpoints (Phase 8) -----

    async def _panel(self, request: web.Request) -> web.Response:
        """Serve the self-contained admin console. The page holds no secrets;
        the admin token is typed in and sent as the Bearer on each API call."""
        return web.Response(text=PANEL_HTML, content_type="text/html")

    async def _admin_tools(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(
            {"tools": [
                {"name": t.name, "module": t.module, "description": t.description}
                for t in self._registry.all()
            ]}
        )

    async def _admin_list_access(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        agent_id = _int_or_none(request.query.get("agent_id"))
        if agent_id is None:
            return web.json_response({"error": "agent_id is required"}, status=400)
        rules = await self._access.list_rules(agent_id)
        return web.json_response(
            {"rules": [
                {"id": r.id, "module": r.module, "tool": r.tool, "allowed": r.allowed}
                for r in rules
            ]}
        )

    async def _admin_add_access(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body, err = await parse_body(request, AddAccessRuleRequest)
        if err is not None:
            return err
        rule = await self._access.add_rule(
            body.agent_id, body.module or None, body.tool or None, body.allowed
        )
        return web.json_response(
            {"id": rule.id, "module": rule.module, "tool": rule.tool, "allowed": rule.allowed},
            status=201,
        )

    async def _admin_delete_access(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        rule_id = _int_or_none(request.match_info.get("id"))
        if rule_id is None:
            return web.json_response({"error": "bad rule id"}, status=400)
        ok = await self._access.delete_rule(rule_id)
        return web.json_response({"ok": ok}, status=200 if ok else 404)

    async def _admin_memory(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if self._memory is None:
            return web.json_response({"facts": []})
        facts = await self._memory.list_facts()
        return web.json_response(
            {"facts": [
                {"id": f.id, "content": f.content, "agent_id": f.agent_id,
                 "created_at": f.created_at.isoformat() if f.created_at else None}
                for f in facts
            ]}
        )

    async def _admin_provenance(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if self._memory is None:
            return web.json_response({"changes": []})
        changes = await self._memory.list_change_log()
        return web.json_response(
            {"changes": [
                {"id": c.id, "entity": c.entity, "entity_id": c.entity_id,
                 "agent_id": c.agent_id, "action": c.action,
                 "old": c.old_content, "new": c.new_content,
                 "at": c.at.isoformat() if c.at else None}
                for c in changes
            ]}
        )

    async def _admin_reminders(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if self._memory is None:
            return web.json_response({"reminders": []})
        reminders = await self._memory.list_reminders(include_done=True)
        return web.json_response(
            {"reminders": [
                {"id": r.id, "text": r.text,
                 "due_at": r.due_at.isoformat() if r.due_at else None,
                 # audience: "agent" rows are notes an agent left ITSELF (it
                 # wakes and decides what to say). The panel is where the owner
                 # goes to cancel one, so it must be distinguishable from a
                 # reminder the owner asked for.
                 "audience": r.audience,
                 "tz": r.tz, "recurrence": r.recurrence, "is_done": r.is_done,
                 "agent_id": r.agent_id}
                for r in reminders
            ]}
        )

    async def _admin_delete_reminder(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        rid = _int_or_none(request.match_info.get("id"))
        if rid is None:
            return web.json_response({"error": "bad reminder id"}, status=400)
        if self._memory is None:
            return web.json_response({"ok": False}, status=404)
        ok = await self._memory.delete_reminder(rid)
        return web.json_response({"ok": ok}, status=200 if ok else 404)

    async def _admin_profile(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        profile = await self._memory.get_profile() if self._memory else None
        if profile is None:
            return web.json_response({"profile": None})
        return web.json_response(
            {"profile": {"timezone": profile.timezone, "home_location": profile.home_location,
                         "agent_id": profile.agent_id}}
        )

    # ----- backups (proxy to the backup service) -----

    async def _backup_proxy(self, method: str, timeout: float) -> web.Response:
        """Call the backup service's API and pass its JSON response through."""
        if not self._backup_url:
            return web.json_response({"error": "backup service not configured"}, status=503)
        url = self._backup_url + "/backups"
        headers = {"Authorization": f"Bearer {self._backup_token}"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
                async with s.request(method, url, headers=headers) as r:
                    body = await r.json()
                    return web.json_response(body, status=r.status)
        except aiohttp.ClientError as e:
            return web.json_response({"error": f"backup service unreachable: {e}"}, status=502)

    async def _admin_backups_list(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await self._backup_proxy("GET", timeout=30)

    async def _admin_backups_create(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        # A backup can take a while (dump + encrypt + upload) — generous timeout.
        return await self._backup_proxy("POST", timeout=600)

    # ----- modules (re-discovery) -----

    async def _admin_modules_refresh(self, request: web.Request) -> web.Response:
        """Re-discover module tools NOW (Step 2 of ARCHITECTURE_REVIEW.md):
        pending (unreachable-at-boot) modules get another RegisterTools attempt,
        live modules can contribute newly added tools — no Brain restart needed.
        NOTE: agents that cache tools/list per session (the API backend) see new
        tools on their next session; the CLI backend sees them next turn."""
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if self._modules_router is None:
            return web.json_response({"error": "no modules configured"}, status=404)
        summary = await self._modules_router.refresh(self._registry)
        return web.json_response(
            {"modules": summary, "tools_total": len(self._registry.all())}
        )

    # ----- enrollment (device pairing) -----

    async def _enroll_request(self, request: web.Request) -> web.Response:
        """An agent asks to connect. Gated by the shared enroll token; still
        needs the owner's approval before a token is issued."""
        if self._enroll is None:
            return web.json_response({"error": "enrollment disabled"}, status=503)
        body, err = await parse_body(request, EnrollRequest)
        if err is not None:
            return err
        # The OWNER'S APPROVAL is the real gate: a pending request grants nothing
        # until a human says yes. ENROLL_TOKEN is optional extra friction — if
        # configured, requests must present it; if empty, anyone on the network
        # may ASK (and sit pending until approved/rejected).
        if self._enroll_token and not secrets.compare_digest(
            body.enroll_token, self._enroll_token
        ):
            return web.json_response({"error": "invalid enroll token"}, status=401)
        status = await self._enroll.request(body.slug, body.secret)
        return web.json_response({"status": status})

    async def _enroll_status(self, request: web.Request) -> web.Response:
        """Agent polls for its decision; the secret authenticates it (in the
        POST body — never the query string). Returns the token exactly once,
        minted at this moment, after approval."""
        if self._enroll is None:
            return web.json_response({"error": "enrollment disabled"}, status=503)
        body, err = await parse_body(request, EnrollStatusRequest)
        if err is not None:
            return err
        status, token = await self._enroll.claim(body.slug, body.secret)
        resp = {"status": status}
        if token is not None:
            resp["token"] = token
        return web.json_response(resp)

    async def _admin_enrollments(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if self._enroll is None:
            return web.json_response({"pending": []})
        pending = await self._enroll.list_pending()
        return web.json_response(
            {"pending": [{"id": e.id, "slug": e.slug,
                          "created_at": e.created_at.isoformat() if e.created_at else None}
                         for e in pending]}
        )

    async def _admin_enroll_approve(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        eid = _int_or_none(request.match_info.get("id"))
        if eid is None or self._enroll is None:
            return web.json_response({"error": "bad request"}, status=400)
        ok = await self._enroll.approve(eid)
        return web.json_response({"ok": ok}, status=200 if ok else 404)

    async def _admin_enroll_reject(self, request: web.Request) -> web.Response:
        if not self._is_admin(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        eid = _int_or_none(request.match_info.get("id"))
        if eid is None or self._enroll is None:
            return web.json_response({"error": "bad request"}, status=400)
        ok = await self._enroll.reject(eid)
        return web.json_response({"ok": ok}, status=200 if ok else 404)

    # ----- MCP -----

    async def _handle_mcp(self, request: web.Request) -> web.Response:
        agent = await self._store.authenticate(_bearer(request))
        if agent is None:
            # A missing id (notification) still shouldn't leak tools — reject.
            return _error(None, INVALID_REQUEST, "unauthorized: unknown or missing agent token")

        try:
            body = await request.json()
        except Exception:
            return _error(None, PARSE_ERROR, "Body is not valid JSON")

        if isinstance(body, list):
            body = body[0] if body else {}

        method = body.get("method")
        req_id = body.get("id")

        if req_id is None:  # notification — no response body
            return web.Response(status=202)

        if method == "initialize":
            client_version = (body.get("params") or {}).get(
                "protocolVersion", DEFAULT_PROTOCOL_VERSION
            )
            return _result(
                req_id,
                {
                    "protocolVersion": client_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "kaizen-brain", "version": "1.0.0"},
                },
            )

        if method == "ping":
            return _result(req_id, {})

        if method == "tools/list":
            # Filter to the tools THIS agent is allowed to call (access-list).
            tools = []
            for t in self._registry.all():
                if await self._access.is_allowed(agent.id, t.module, t.name):
                    tools.append(t.to_mcp_schema())
            return _result(req_id, {"tools": tools})

        if method == "tools/call":
            params = body.get("params") or {}
            name = params.get("name", "")
            arguments = params.get("arguments") or {}

            tool = self._registry.get(name)
            if tool is None:
                # Unknown tool -> isError result (agent can recover), not a crash.
                return _result(
                    req_id,
                    {"content": [{"type": "text", "text": f"Error: unknown tool '{name}'"}],
                     "isError": True},
                )

            # Gate on the access-list BEFORE running anything.
            if not await self._access.is_allowed(agent.id, tool.module, name):
                logger.warning("Access DENIED: agent '%s' -> %s", agent.slug, name)
                return _result(
                    req_id,
                    {"content": [{"type": "text",
                                  "text": f"Error: agent '{agent.slug}' is not allowed to call '{name}'"}],
                     "isError": True},
                )

            logger.info("MCP call by agent '%s': %s(%s)", agent.slug, name, arguments)
            # Publish the acting agent (id for provenance stamping, slug for the
            # module gRPC agent_id) so writes are attributed and module proxies
            # forward the identity. ALWAYS reset — a ContextVar leak would
            # misattribute the next call.
            id_token = current_actor_id.set(agent.id)
            slug_token = current_actor_slug.set(agent.slug)
            try:
                # Structured outcome (Step 5): the registry says is_error
                # explicitly — no more sniffing the text for an "Error:" prefix.
                result = await self._registry.execute(name, arguments)
                text, is_error = result.text, result.is_error
            except Exception as e:  # registry traps most; belt-and-braces
                logger.exception("MCP tool call failed: %s", name)
                text, is_error = f"Error: {e}", True
            finally:
                current_actor_id.reset(id_token)
                current_actor_slug.reset(slug_token)
            return _result(
                req_id,
                {"content": [{"type": "text", "text": text}], "isError": is_error},
            )

        if method in ("resources/list", "prompts/list"):
            key = "resources" if method.startswith("resources") else "prompts"
            return _result(req_id, {key: []})

        return _error(req_id, METHOD_NOT_FOUND, f"Method not supported: {method}")
