# =============================================================================
# Tracker API — modules/tracker/api.py
# =============================================================================
# WHAT: The HTTP contract every participant speaks — Kaya (admin) delegates and
#       observes; each project's agents (project-auth) claim and report. Built
#       on aiohttp, mirroring the request-handling style of brain/server.py.
#
# WHY bearer tokens (unlike the MCP server):
#   The MCP server is localhost-only and trusted. The tracker binds 0.0.0.0 so
#   external project containers can reach it, which means it MUST authenticate.
#   Two token kinds:
#     - admin token (TRACKER_ADMIN_TOKEN) → Kaya's routes (register/delegate/observe)
#     - per-project token (generated on register) → that project's claim/report
#
# THE CONTRACT (both integration tiers are documented in docs/tracker-integration.md):
#   GET  /health                       none     liveness
#   POST /projects                     admin    register → {project, token}
#   GET  /projects                     admin    list projects
#   POST /tasks                        admin    delegate {project, title, description}
#   GET  /tasks?project=&status=       admin    observe
#   GET  /tasks/{id}                   admin/proj  read one
#   POST /projects/{project}/claim     project  atomically claim next queued (or 204)
#   POST /tasks/{id}/report            project  update {status, summary?, artifacts?, error?}
#   POST /projects/{project}/approve   admin    approve an enrolling project (v2)
#   POST /projects/{project}/rotate    admin    invalidate its token (v2, case 16)
#   POST /projects/{project}/restart   admin    ask a wedged project to restart itself
#   GET  /questions                    admin    questions awaiting the owner (v2)
#   POST /questions/{id}/answer        admin    answer one, unblocking its agent (v2)
#   GET  /activity                     admin    live per-agent status, all projects (v2)
#   POST /projects/{project}/reprioritise  admin  reorder its queue (v2)
#   POST /tasks/{id}/cancel            admin    abort it, telling the project (v2)
#   POST /tasks/{id}/requeue           admin    unstick it back into the queue (v2)
#
# WHY the routes still say /tasks while the model says Directive: this surface
#   IS the v1 poller contract, and a project running a 30-line poller shouldn't
#   have to be redeployed because the Hub grew a richer vocabulary. The payloads
#   gained fields (kind, priority, task_id); the paths deliberately did not move.
#   Agents never come through here — they reach the Hub's tools over gRPC.
# =============================================================================

import logging

from aiohttp import web

from modules.tracker.config import settings
from modules.tracker import store
from modules.tracker.models import AGENT_KINDS, DIRECTIVE_KINDS, DIRECTIVE_STATUSES
from modules.tracker.panel import panel

logger = logging.getLogger(__name__)


def _bearer(request: web.Request) -> str:
    """Extract the bearer token from the Authorization header ('' if absent)."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return ""


def _is_admin(request: web.Request) -> bool:
    """True only when a non-empty admin token is configured AND matches.

    The empty-token guard matters: without it, an unset TRACKER_ADMIN_TOKEN
    would make every '' == '' request admin — a wide-open door.
    """
    admin = settings.tracker_admin_token
    return bool(admin) and _bearer(request) == admin


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Admin routes (Kaya)
# ---------------------------------------------------------------------------
async def register_project(request: web.Request) -> web.Response:
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    body = await _json(request)
    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    max_concurrent = _int_field(body, "max_concurrent", 1)
    if max_concurrent is None or max_concurrent < 1:
        return web.json_response(
            {"error": "max_concurrent must be a positive integer"}, status=400
        )

    try:
        project = await store.create_project(
            name,
            body.get("description"),
            purpose=body.get("purpose"),
            grpc_addr=body.get("grpc_addr"),
            max_concurrent=max_concurrent,
        )
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=409)

    # The token is returned exactly once, here — the caller must store it.
    return web.json_response(
        {
            "project": {"id": project.id, "name": project.name},
            "token": project.token,
        },
        status=201,
    )


async def list_projects(request: web.Request) -> web.Response:
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)
    projects = await store.list_projects()
    # Tokens are secrets — never list them; they're shown only at register time.
    # to_dict() deliberately omits `token` for exactly this reason.
    return web.json_response({"projects": [p.to_dict() for p in projects]})


async def create_task(request: web.Request) -> web.Response:
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    body = await _json(request)
    project_name = (body.get("project") or "").strip()
    title = (body.get("title") or "").strip()
    if not project_name or not title:
        return web.json_response(
            {"error": "project and title are required"}, status=400
        )

    kind = (body.get("kind") or "develop").strip()
    if kind not in DIRECTIVE_KINDS:
        return web.json_response(
            {"error": f"kind must be one of {list(DIRECTIVE_KINDS)}"}, status=400
        )

    # Lower runs first, so 0 is the MOST urgent Directive a caller can ask for
    # — it must survive as 0 and not be defaulted away.
    priority = _int_field(body, "priority", 100)
    if priority is None:
        return web.json_response({"error": "priority must be an integer"}, status=400)

    project = await store.get_project_by_name(project_name)
    if project is None:
        return web.json_response(
            {"error": f"no project named '{project_name}'"}, status=404
        )

    directive = await store.create_directive(
        project.id,
        title,
        body.get("description"),
        kind=kind,
        priority=priority,
        task_id=body.get("task_id"),
    )
    return web.json_response(
        {"task_id": directive.id, "status": directive.status}, status=201
    )


async def list_tasks(request: web.Request) -> web.Response:
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    project_id = None
    project_name = request.query.get("project")
    if project_name:
        project = await store.get_project_by_name(project_name)
        if project is None:
            return web.json_response(
                {"error": f"no project named '{project_name}'"}, status=404
            )
        project_id = project.id

    status = request.query.get("status")
    if status and status not in DIRECTIVE_STATUSES:
        return web.json_response(
            {"error": f"status must be one of {list(DIRECTIVE_STATUSES)}"}, status=400
        )

    directives = await store.list_directives(project_id=project_id, status=status)
    return web.json_response({"tasks": [d.to_dict() for d in directives]})


async def get_task(request: web.Request) -> web.Response:
    """Readable by admin, or by the project that owns the task."""
    task_id = _int_or_none(request.match_info.get("id"))
    if task_id is None:
        return web.json_response({"error": "bad task id"}, status=400)

    directive = await store.get_directive(task_id)
    if directive is None:
        return web.json_response({"error": "task not found"}, status=404)

    if not _is_admin(request):
        project = await store.get_project_by_token(_bearer(request))
        if project is None:
            # No usable credential at all — that is an authentication problem
            # and the caller deserves to be told so.
            return web.json_response(
                {"error": "admin or project token required"}, status=401
            )
        if project.id != directive.project_id:
            # A VALID token for a DIFFERENT project gets exactly the answer it
            # would get for a Directive that doesn't exist. Otherwise 401-vs-404
            # is an oracle for "does Directive #N exist?" across the project
            # boundary — the same leak report() deliberately closes below.
            return web.json_response({"error": "task not found"}, status=404)

    return web.json_response({"task": directive.to_dict()})


async def approve_project(request: web.Request) -> web.Response:
    """Approve a project that asked to enroll (architecture §5, step 4).

    Mints nothing: the token comes into existence inside the approved Warden's
    next Register, and only for the claimant holding the matching enrollment
    secret. So this route hands out no credential and there is nothing here for
    a leaked admin token to steal beyond the approval itself.
    """
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    name = (request.match_info.get("project") or "").strip()
    project = await store.approve_project(name)
    if project is None:
        return web.json_response(
            {"error": f"no project named '{name}' awaiting approval"}, status=404
        )
    return web.json_response({"project": project.to_dict()})


async def rotate_project(request: web.Request) -> web.Response:
    """Invalidate a project's token (architecture §7 case 16).

    This is the recovery path the Hub points a token-less Warden at when it
    tries to re-enroll an already-active project: only the owner may decide
    that the credential out there is no longer good. Pass
    `{"reset_secret": true}` when the container itself is suspect — see
    store.rotate_project_token for why that is not the default.
    """
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    name = (request.match_info.get("project") or "").strip()
    body = await _json(request, allow_empty=True)
    project = await store.rotate_project_token(
        name, reset_secret=bool(body.get("reset_secret"))
    )
    if project is None:
        return web.json_response({"error": f"no project named '{name}'"}, status=404)
    return web.json_response({"project": project.to_dict()})


async def reprioritise_project(request: web.Request) -> web.Response:
    """Rewrite a project's queue order (the panel's drag-to-reorder)."""
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    name = (request.match_info.get("project") or "").strip()
    project = await store.get_project_by_name(name)
    if project is None:
        return web.json_response({"error": f"no project named '{name}'"}, status=404)

    body = await _json(request)
    raw = body.get("ordered_ids")
    if not isinstance(raw, list):
        return web.json_response({"error": "ordered_ids must be a list"}, status=400)
    try:
        ordered = [int(i) for i in raw]
    except (TypeError, ValueError):
        return web.json_response({"error": "ordered_ids must be integers"}, status=400)

    moved = await store.reprioritise(project.id, ordered)
    return web.json_response({"moved": moved})


# ---------------------------------------------------------------------------
# Directive controls (admin) — unsticking things from the browser
# ---------------------------------------------------------------------------
def _directive_controls(dispatcher):
    """Build the cancel/requeue handlers over a dispatcher.

    A closure rather than module-level functions because cancelling has to
    reach the project's Warden, and the dispatcher is what owns that channel.
    Without one (tests, a read-only boot) cancelling still updates the Hub's
    row and says the project was not told, rather than pretending it was.
    """

    async def cancel(request: web.Request) -> web.Response:
        if not _is_admin(request):
            return web.json_response({"error": "admin token required"}, status=401)
        directive_id = _int_or_none(request.match_info.get("id"))
        if directive_id is None:
            return web.json_response({"error": "bad task id"}, status=400)

        directive = await store.get_directive(directive_id)
        if directive is None:
            return web.json_response({"error": "task not found"}, status=404)
        if directive.status in ("done", "failed", "cancelled"):
            # The transition map treats cancelled -> cancelled as an idempotent
            # no-op, which is right for a retrying project but wrong here: a
            # panel that answers 200 to a click that did nothing is lying to the
            # owner. 409 with the actual state lets them see why.
            return web.json_response(
                {"error": f"directive #{directive_id} is already {directive.status}"},
                status=409,
            )

        body = await _json(request, allow_empty=True)
        reason = (body.get("reason") or "cancelled from the panel").strip()
        project = await store.get_project(directive.project_id)
        told = False
        if dispatcher is not None and project is not None:
            told = await dispatcher.cancel(project, directive, reason)
        try:
            updated = await store.set_status(directive_id, "cancelled", summary=reason)
        except store.TransitionError as e:
            return web.json_response({"error": str(e)}, status=409)
        return web.json_response({"task": updated.to_dict(), "project_told": told})

    async def requeue(request: web.Request) -> web.Response:
        """Put a stuck Directive back in the queue.

        The escape hatch for the case the sweeper cannot see: the owner decides
        a Directive is stuck, so the lease and the claimant are cleared and the
        dispatcher offers it to the project again on its next pass.

        WHAT THIS DOES NOT DO, deliberately: it does not dial the Warden. So a
        project whose pipeline is still ALIVE answers the re-dispatch with "we
        already have it" (infra/wardenkit accepts a re-Dispatch of a running
        Directive on purpose — cases 8 and 9 depend on it) and nothing
        restarts; this route unsticks the Hub's row, not the project's process.
        To make a live-but-wedged fleet start over, cancel it and send it again.
        Dialing from here would be worse than useless: a Cancel the Warden
        accepts makes it push its own `cancelled` report, which would race this
        write and land AFTER it — leaving the Directive cancelled by the very
        click that asked for it to run again.
        """
        if not _is_admin(request):
            return web.json_response({"error": "admin token required"}, status=401)
        directive_id = _int_or_none(request.match_info.get("id"))
        if directive_id is None:
            return web.json_response({"error": "bad task id"}, status=400)
        try:
            updated = await store.set_status(directive_id, "queued", claimed_by="")
        except store.TransitionError as e:
            return web.json_response({"error": str(e)}, status=409)
        if updated is None:
            return web.json_response({"error": "task not found"}, status=404)
        return web.json_response({"task": updated.to_dict()})

    return cancel, requeue


async def activity(request: web.Request) -> web.Response:
    """Live per-agent status across every project — the panel's fleet view."""
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)
    rows = await store.live_agent_status()
    return web.json_response(
        {
            "activity": [
                {
                    **status.to_dict(),
                    "project": project.name,
                    "directive": {
                        "id": directive.id,
                        "title": directive.title,
                        "status": directive.status,
                        "task_id": directive.task_id,
                    },
                }
                for status, directive, project in rows
            ]
        }
    )


# ---------------------------------------------------------------------------
# Questions (admin) — the owner's half of a blocking AskOwner
# ---------------------------------------------------------------------------
async def list_questions(request: web.Request) -> web.Response:
    """Questions still waiting on the owner, oldest first."""
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    rows = await store.pending_questions()
    return web.json_response(
        {
            "questions": [
                {
                    **q.to_dict(),
                    "directive": {"id": d.id, "title": d.title, "status": d.status},
                    "project": p.name,
                }
                for q, d, p in rows
            ]
        }
    )


async def answer_question(request: web.Request) -> web.Response:
    """Answer a question. The agent's held-open AskOwner RPC returns within one
    poll interval — this route only has to write the row."""
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    question_id = _int_or_none(request.match_info.get("id"))
    if question_id is None:
        return web.json_response({"error": "bad question id"}, status=400)

    body = await _json(request)
    answer = body.get("answer")
    if not isinstance(answer, str):
        return web.json_response({"error": "answer (string) is required"}, status=400)

    question = await store.answer_question(question_id, answer)
    if question is None:
        # Unknown, or already answered — answering twice is ambiguous for an
        # agent that has already resumed on the first answer.
        return web.json_response(
            {"error": "no unanswered question with that id"}, status=404
        )
    return web.json_response({"question": question.to_dict()})


# ---------------------------------------------------------------------------
# Agents / team roster (admin)
# ---------------------------------------------------------------------------
async def list_agents(request: web.Request) -> web.Response:
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    project_id = None
    project_name = request.query.get("project")
    if project_name:
        project = await store.get_project_by_name(project_name)
        if project is None:
            return web.json_response(
                {"error": f"no project named '{project_name}'"}, status=404
            )
        project_id = project.id

    agents = await store.list_agents(project_id=project_id)
    return web.json_response({"agents": [a.to_dict() for a in agents]})


async def usage(request: web.Request) -> web.Response:
    """Token/cost totals by (project, agent) (§8, "Scaling the fleet"). Fetched
    unscoped like `/tasks` and `/agents` — the panel filters client-side per
    project. Backs the Analytics tab: the data that answers "did the crew
    cost more than one agent would have"."""
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)
    totals = await store.usage_totals()
    return web.json_response({"usage": totals})


async def register_agent(request: web.Request) -> web.Response:
    if not _is_admin(request):
        return web.json_response({"error": "admin token required"}, status=401)

    body = await _json(request)
    project_name = (body.get("project") or "").strip()
    name = (body.get("name") or "").strip()
    if not project_name or not name:
        return web.json_response({"error": "project and name are required"}, status=400)

    kind = (body.get("kind") or "ai").strip()
    if kind not in AGENT_KINDS:
        return web.json_response(
            {"error": f"kind must be one of {list(AGENT_KINDS)}"}, status=400
        )

    project = await store.get_project_by_name(project_name)
    if project is None:
        return web.json_response(
            {"error": f"no project named '{project_name}'"}, status=404
        )

    try:
        agent = await store.create_agent(
            project.id, name, body.get("role"), kind, body.get("model"),
            tier=body.get("tier"), area=body.get("area"),
            reports_to=body.get("reports_to"),
        )
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=409)

    return web.json_response({"agent": agent.to_dict()}, status=201)


# ---------------------------------------------------------------------------
# Project routes (the agents)
# ---------------------------------------------------------------------------
async def claim(request: web.Request) -> web.Response:
    project = await store.get_project_by_token(_bearer(request))
    if project is None:
        return web.json_response({"error": "valid project token required"}, status=401)

    # The URL names a project too; it must match the token's project so a token
    # can't be used to claim from someone else's queue.
    url_name = request.match_info.get("project")
    if url_name != project.name:
        return web.json_response(
            {"error": "token does not match the project in the URL"}, status=403
        )

    body = await _json(request, allow_empty=True)
    agent = (body.get("agent") or "agent").strip() or "agent"

    await store.touch_project(project.id)
    directive = await store.claim_next(project.id, agent)
    if directive is None:
        # Nothing queued — 204 is the poller's "sleep and retry" signal.
        return web.Response(status=204)
    return web.json_response({"task": directive.to_dict()})


async def report(request: web.Request) -> web.Response:
    project = await store.get_project_by_token(_bearer(request))
    if project is None:
        return web.json_response({"error": "valid project token required"}, status=401)

    task_id = _int_or_none(request.match_info.get("id"))
    if task_id is None:
        return web.json_response({"error": "bad task id"}, status=400)

    body = await _json(request)
    status = (body.get("status") or "").strip()
    if status not in DIRECTIVE_STATUSES:
        return web.json_response(
            {"error": f"status must be one of {list(DIRECTIVE_STATUSES)}"}, status=400
        )

    artifacts = body.get("artifacts")
    if artifacts is not None and not isinstance(artifacts, list):
        return web.json_response(
            {"error": "artifacts must be a list of {type, url}"}, status=400
        )

    await store.touch_project(project.id)
    try:
        directive = await store.report_directive(
            task_id,
            project.id,
            status=status,
            summary=body.get("summary"),
            artifacts=artifacts,
            error=body.get("error"),
            task_id=body.get("task_id"),
        )
    except store.TransitionError as e:
        # 409, not 400: the request is well-formed, it just doesn't fit the
        # Directive's current state. A poller reading this knows to re-read the
        # Directive rather than to fix its payload.
        return web.json_response({"error": str(e)}, status=409)

    if directive is None:
        # Either no such Directive, or it belongs to another project — same
        # answer, so a token can't probe for the existence of others' work.
        return web.json_response({"error": "task not found for this project"}, status=404)

    return web.json_response({"task": directive.to_dict()})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _json(request: web.Request, allow_empty: bool = False) -> dict:
    """Parse a JSON body, tolerating an empty body when allowed."""
    if allow_empty and not request.can_read_body:
        return {}
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_field(body: dict, key: str, default: int) -> int | None:
    """Read an optional integer body field. None means "present but not a number".

    WHY not the obvious `int(body.get(key) or default)`: it is wrong twice. It
    swallows a legitimate 0 (priority 0 is the most urgent Directive there is,
    and it would silently become the default), and it raises ValueError on a
    non-numeric value — which aiohttp turns into a 500 for what is really the
    caller's mistake. Returning None lets the route answer 400 instead.
    """
    raw = body.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def build_app(dispatcher=None) -> web.Application:
    """Assemble the aiohttp application with every route wired up.

    `dispatcher` is threaded in only so the panel's cancel button can reach the
    project's Warden through the same channel cache the dispatcher keeps.
    """
    cancel_directive, requeue_directive = _directive_controls(dispatcher)

    async def restart_project(request: web.Request) -> web.Response:
        """Ask a project's Warden to restart itself. Admin only.

        The Hub relays; it never restarts anything. An unreachable Warden is a
        200 with `accepted: false` and a reason, not a 5xx — "the process is
        gone, this needs the host" is an answer the caller must be able to read.
        """
        if not _is_admin(request):
            return web.json_response({"error": "admin token required"}, status=401)
        name = (request.match_info.get("project") or "").strip()
        project = await store.get_project_by_name(name)
        if project is None:
            return web.json_response({"error": "unknown project"}, status=404)
        if dispatcher is None:
            return web.json_response({"error": "dispatcher not running"}, status=503)
        body = await _json(request, allow_empty=True)
        result = await dispatcher.restart(
            project,
            reason=str(body.get("reason") or ""),
            scope=str(body.get("scope") or "jobs"),
            requested_by="admin api",
        )
        requeued = []
        for did in result.get("dropped") or []:
            try:
                await store.set_status(did, "queued", summary="requeued after restart")
                requeued.append(did)
            except store.TransitionError:
                pass
        return web.json_response({**result, "requeued": requeued})

    app = web.Application()
    # Read-only web dashboard (static HTML; the admin token is entered in the
    # browser and sent as the Bearer to the admin endpoints below).
    app.router.add_get("/", panel)
    app.router.add_get("/panel", panel)
    app.router.add_get("/health", health)
    app.router.add_post("/projects", register_project)
    app.router.add_get("/projects", list_projects)
    app.router.add_post("/projects/{project}/approve", approve_project)
    app.router.add_post("/projects/{project}/rotate", rotate_project)
    app.router.add_post("/tasks", create_task)
    app.router.add_get("/tasks", list_tasks)
    app.router.add_get("/tasks/{id}", get_task)
    app.router.add_get("/agents", list_agents)
    app.router.add_post("/agents", register_agent)
    app.router.add_get("/usage", usage)
    app.router.add_get("/questions", list_questions)
    app.router.add_post("/questions/{id}/answer", answer_question)
    app.router.add_get("/activity", activity)
    app.router.add_post("/projects/{project}/reprioritise", reprioritise_project)
    app.router.add_post("/projects/{project}/restart", restart_project)
    app.router.add_post("/tasks/{id}/cancel", cancel_directive)
    app.router.add_post("/tasks/{id}/requeue", requeue_directive)
    app.router.add_post("/projects/{project}/claim", claim)
    app.router.add_post("/tasks/{id}/report", report)
    return app
