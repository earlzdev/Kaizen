# =============================================================================
# Трекер module entrypoint (Phase 9) — modules/tracker/main.py
# =============================================================================
# WHAT: Boots the STANDALONE tracker module. It:
#   1. ensures the tracker's OWN database exists (creates it if not),
#   2. creates its tables (metadata.create_all — it owns exactly these tables),
#   3. runs two background loops (dispatcher.py hands queued Directives to
#      projects' Wardens; sweeper.py requeues the ones whose project died),
#   4. serves THREE faces concurrently:
#        - gRPC Module contract (RegisterTools/CallTool) — for Brain/agents
#        - gRPC Hub contract (Register/PushStatus/PushReport/AskOwner/
#          Heartbeat) — for projects' Wardens (tracker v2)
#        - HTTP API (register/delegate/claim/report + dashboard) — for the
#          external project pollers, unchanged from the v1 tracker.
#
# WHY all faces in one process: the tracker owns one dataset. Agents reach it
#       through Brain over gRPC, projects' Wardens reach it over their own gRPC
#       port with per-project tokens, and the poller tier reaches the SAME store
#       over HTTP. One service, one DB, three front doors — no split data. It
#       also matters for AskOwner: a question held open on the Hub port is
#       answered through the Module port or the HTTP panel, and the shared
#       database is what lets those three meet.
#
# WHY create_tables (not Alembic): the tracker owns exactly its few tables and
#       nothing else, so a plain metadata.create_all is the honest tool (as the
#       v1 tracker did) — no migration chain needed.
#
# HOW it runs: `python -m modules.tracker.main` (the `tracker` service in
#       docker-compose). Brain discovers it via BRAIN_MODULES="...,tracker=tracker:9103".
# =============================================================================

import asyncio
import logging

import asyncpg
import grpc
from aiohttp import web

from infra.config_checks import is_placeholder
from infra.modkit import ModuleServicer
from modules.tracker import store
from modules.tracker.api import build_app
from modules.tracker.config import settings
from modules.tracker.dispatcher import Dispatcher
from modules.tracker.hub_grpc import serve_hub
from modules.tracker.notify import Notifier
from modules.tracker.sweeper import LeaseSweeper
from modules.tracker.tools import build_tools
from infra.proto.gen import module_pb2_grpc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def ensure_database() -> None:
    """Create the tracker's own database if it doesn't exist yet (idempotent)."""
    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_maintenance_db,
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", settings.tracker_postgres_db
        )
        if exists:
            logger.info("Tracker database '%s' already exists.", settings.tracker_postgres_db)
            return
        await conn.execute(f'CREATE DATABASE "{settings.tracker_postgres_db}"')
        logger.info("Created tracker database '%s'.", settings.tracker_postgres_db)
    finally:
        await conn.close()


async def main() -> None:
    logger.info("Трекер module (Phase 9, standalone) starting...")
    if not settings.tracker_admin_token:
        logger.warning(
            "TRACKER_ADMIN_TOKEN is empty — admin routes (register/delegate) reject "
            "every request. Set it in .env."
        )
    await ensure_database()
    await store.create_tables()

    # How the Hub reaches the owner: every surface below shares ONE notifier, so
    # a report, a question and a requeue all travel the same path and there is a
    # single place to look when the owner stops hearing about things.
    notifier = Notifier(settings.brain_url, settings.module_event_token)
    # Swallowing a delivery failure at RUNTIME is right and stays that way — a
    # notification outage must not fail a report an hour of work produced. But
    # "never configured" is not a runtime failure, it is a deployment mistake,
    # and it must be loud where deployment mistakes are found. Left as a warning
    # in a log nobody reads, it cost five lost reports on the first real
    # project — including two finished answers the owner never saw.
    #
    # The Hub refuses to boot (Brain only warns): a tracker that cannot reach the
    # owner is a queue that silently swallows everything it is given.
    if is_placeholder(settings.module_event_token):
        raise SystemExit(
            "\n"
            "=========================================================\n"
            " MODULE_EVENT_TOKEN is empty or still a template value.\n"
            " Every report, question and requeue would be DROPPED —\n"
            " the owner would simply never hear from this project.\n"
            " Set the SAME value in .env for both Brain and tracker,\n"
            " then check it end to end:  make notify-selftest\n"
            "=========================================================\n"
        )

    # Built BEFORE the faces that use it: both the tool surface and the panel's
    # cancel button have to reach a project's Warden, and both must do it
    # through the ONE channel cache the dispatcher keeps rather than each
    # opening its own connection to every project.
    dispatcher = Dispatcher(notifier.send, notifier.send_tunnel_message)
    sweeper = LeaseSweeper(notifier.send)

    # HTTP face (external pollers + dashboard). The dispatcher goes in so the
    # panel's cancel button reaches the project's Warden, not just our own row.
    http_runner = web.AppRunner(build_app(dispatcher), access_log=None)
    await http_runner.setup()
    await web.TCPSite(http_runner, "0.0.0.0", settings.tracker_http_port).start()
    logger.info("Tracker HTTP API on http://0.0.0.0:%d", settings.tracker_http_port)

    # gRPC Module face (Brain/agents).
    grpc_server = grpc.aio.server()
    module_pb2_grpc.add_ModuleServicer_to_server(
        ModuleServicer(settings.tracker_module_name, build_tools(dispatcher)), grpc_server
    )
    grpc_server.add_insecure_port(settings.tracker_module_bind_addr)
    await grpc_server.start()
    logger.info("Tracker Module gRPC on %s", settings.tracker_module_bind_addr)

    # gRPC Hub face (projects' Wardens). Started last of the three, so the
    # store and the other faces are ready before any project can enroll.
    hub_server = await serve_hub(
        settings.tracker_hub_bind_addr, notifier.send, notifier.send_tunnel_message
    )

    # The two background loops that make the Hub active rather than merely
    # reactive: one hands queued work OUT to Wardens, the other notices when a
    # Warden holding work has died. Strong references kept — asyncio holds only
    # a weak one, and a garbage-collected dispatcher would look like a stall.
    dispatch_task = asyncio.create_task(dispatcher.run_forever())
    sweep_task = asyncio.create_task(sweeper.run_forever())

    try:
        await grpc_server.wait_for_termination()
    finally:
        # Cancel BOTH loops and then WAIT for them to unwind before closing the
        # channels they dial through. cancel() only *schedules* a cancellation:
        # closing first would pull a channel out from under an in-flight
        # Dispatch, and a task nobody awaits never has its exception retrieved —
        # so a shutdown failure would surface as a stray "Task exception was
        # never retrieved" from the garbage collector instead of a log line.
        for task in (dispatch_task, sweep_task):
            task.cancel()
        await asyncio.gather(dispatch_task, sweep_task, return_exceptions=True)
        await dispatcher.close()
        await hub_server.stop(grace=5)
        await grpc_server.stop(grace=5)
        await http_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
