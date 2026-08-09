# =============================================================================
# Brain entrypoint — brain/main.py
# =============================================================================
# WHAT: Boots Brain as its own process/container. It:
#   1. ensures Brain's OWN database exists (creates it if not),
#   2. creates its tables straight from the models (metadata.create_all),
#   3. discovers + proxies module tools (BRAIN_MODULES) into the registry,
#   4. serves the MCP front door on 0.0.0.0:<brain_port>,
#   5. runs the reminder sweeper (pushes due reminders to agents).
#
# WHY it creates its own DB: each v2 service has a dedicated logical database,
#   but the Postgres container only auto-creates ONE (POSTGRES_DB). So Brain
#   connects to the maintenance DB ("postgres") and issues CREATE DATABASE for
#   its own. Idempotent: skipped if it already exists.
#
# WHY create_all, not migrations: pre-prod decision (owner's call) — the models
#   ARE the schema; no Alembic chains to maintain. create_all is idempotent
#   (only creates what's missing). If a column changes shape later, wipe the
#   volume or ALTER by hand — acceptable until real prod data exists.
#
# WHY it warns on a missing admin token: without BRAIN_ADMIN_TOKEN the /admin
#   routes reject everything, so no agents can be minted and no one can call the
#   MCP endpoint. Better a loud warning at boot than a silent 401 later.
#
# HOW it runs: `python -m brain.main` (the `brain` service in docker-compose).
# =============================================================================

import asyncio
import logging
import signal

import asyncpg
from aiohttp import web
from sqlalchemy import text

from infra.config_checks import is_placeholder

from brain.access import AccessControl
from brain.agents import AgentStore
from brain.config import settings
from brain.delivery import DeliveryClient
from brain.embedder import Embedder
from brain.enroll import EnrollmentStore
from brain.episodes import EpisodeStore
from brain.memory import MemoryStore
from brain.module_client import ModuleClient
from brain.modules import ModuleRouter, parse_modules
from brain.notes import NoteStore
from brain.server import BrainServer
from brain.sweeper import ReminderSweeper
from brain.tools import build_registry
from brain.tracker_client import TrackerClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def ensure_database() -> None:
    """Create Brain's own database if it doesn't exist yet (idempotent).

    Connects to the maintenance DB with the plain (non-async) asyncpg API —
    CREATE DATABASE cannot run inside a transaction block, and we want no ORM
    machinery here, just a one-shot admin command."""
    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_maintenance_db,
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", settings.brain_postgres_db
        )
        if exists:
            logger.info("Brain database '%s' already exists.", settings.brain_postgres_db)
            return
        # Identifier can't be parameterised; the name comes from our own config,
        # not user input, and is quoted to be safe.
        await conn.execute(f'CREATE DATABASE "{settings.brain_postgres_db}"')
        logger.info("Created Brain database '%s'.", settings.brain_postgres_db)
    finally:
        await conn.close()


async def create_tables() -> None:
    """Bring Brain's database to the latest migration.

    Migrations, not create_all: `metadata.create_all` creates MISSING TABLES and
    nothing else, so every column added to a model after a database was first
    created stayed missing forever — silently, until a query named it. That cost
    a production outage. The `CREATE EXTENSION vector` that used to live here
    runs inside the migration environment now, ahead of the first revision.
    """
    from infra.migrations.runner import upgrade

    await upgrade("brain")


async def main() -> None:
    logger.info("Brain starting...")
    await ensure_database()
    await create_tables()

    if not settings.brain_admin_token:
        logger.warning(
            "BRAIN_ADMIN_TOKEN is empty — /admin routes reject every request, so "
            "no agents can be minted. Set it in .env."
        )

    # Warm the embedder NOW (Step 6): the ~120 MB model load used to happen on
    # the first owner message — a hidden multi-second stall of the whole loop.
    # warmup() also asserts the model's dimension against EMBED_DIM.
    embedder = Embedder()
    await embedder.warmup()
    episodes = EpisodeStore(embedder)
    notes = NoteStore(embedder)
    registry = build_registry(embedder, episodes, notes)

    # Discover module tools and merge them in (Phase 4). A module tool then
    # behaves like a built-in over MCP: same tools/list, tools/call, access-list.
    # A module that is unreachable right now is NOT lost: the retry task below
    # keeps asking until it registers (Step 2 of ARCHITECTURE_REVIEW.md — compose
    # only guarantees the container STARTED, not that its gRPC port is bound).
    modules = parse_modules(settings.brain_modules)
    router = None
    if modules:
        router = ModuleRouter(modules, ModuleClient())
        added = await router.register_into(registry)
        logger.info("Registered %d module tool(s) from %d module(s)", added, len(modules))

    agent_store = AgentStore()
    # One push client, shared by the reminder sweeper and POST /event: both do
    # exactly the same thing (Brain -> an agent's delivery_addr), so they should
    # not each carry their own idea of the token and the timeout.
    delivery_client = DeliveryClient(
        settings.delivery_token, timeout=settings.delivery_timeout
    )
    # None (not an empty-token client) when unconfigured, so the dashboard
    # routes answer a clean 503 instead of every call failing tracker-side auth.
    tracker_client = (
        TrackerClient(settings.tracker_http_url, settings.tracker_admin_token)
        if settings.tracker_admin_token
        else None
    )
    server = BrainServer(
        registry=registry,
        store=agent_store,
        access=AccessControl(),
        admin_token=settings.brain_admin_token,
        memory=MemoryStore(embedder),  # admin panel reads facts + provenance
        backup_url=settings.backup_url,
        backup_token=settings.backup_token,
        enroll=EnrollmentStore(agent_store),
        enroll_token=settings.enroll_token,
        modules_router=router,  # POST /admin/modules/refresh re-discovers on demand
        delivery=delivery_client,
        module_event_token=settings.module_event_token,
        default_delivery_slug=settings.default_delivery_agent_slug,
        tracker=tracker_client,
    )
    # A banner, not a boot refusal: Brain has other jobs (memory, tools, the
    # panel) that stay useful without module events, whereas the tracker's whole
    # purpose is telling the owner things — which is why it refuses instead.
    # Loud either way: this used to be one warning line among hundreds, and a
    # deployment mistake that silently drops every report is not a log-level-INFO
    # kind of problem. `${VAR:?}` and `if not value` both catch UNSET and neither
    # catches a `.env` copied from the template, so check for the template too.
    if is_placeholder(settings.module_event_token):
        logger.error(
            "\n"
            "=========================================================\n"
            " MODULE_EVENT_TOKEN is empty or still a template value.\n"
            " POST /event rejects everything, so the tracker CANNOT\n"
            " tell you about reports, questions or requeues.\n"
            " Set the SAME value in .env on Brain and the tracker.\n"
            "========================================================="
        )
    if not settings.enroll_token:
        logger.info(
            "ENROLL_TOKEN not set — enrollment runs in approval-only mode "
            "(any agent on the network may ASK; your approval is the gate)."
        )

    runner = web.AppRunner(server.build_app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.brain_port)
    await site.start()
    logger.info(
        "Brain MCP serving on http://0.0.0.0:%d (%d tools)",
        settings.brain_port,
        len(registry.all()),
    )

    # Start the reminder sweeper (Phase 6): fire due reminders to agents.
    if not settings.delivery_token:
        logger.warning(
            "DELIVERY_TOKEN is empty — reminder pushes will be rejected by agents. "
            "Set it (same value) on Brain and every agent."
        )
    sweeper = ReminderSweeper(
        memory=MemoryStore(embedder),
        agents=AgentStore(),
        delivery=delivery_client,
        interval_seconds=settings.reminder_sweep_seconds,
        default_delivery_slug=settings.default_delivery_agent_slug,
    )
    sweeper_task = asyncio.create_task(sweeper.run_forever())
    # Conversation-archive retention: keep a year, purge daily.
    retention_task = asyncio.create_task(episodes.run_retention_forever())
    # Keep retrying modules that weren't up yet; exits once all registered.
    module_retry_task = (
        asyncio.create_task(
            router.retry_pending_forever(registry, settings.module_retry_seconds)
        )
        if router is not None
        else None
    )

    # SIGTERM-clean shutdown (Step 8): `docker stop` sends SIGTERM, which by
    # default kills Python instantly — the finally block below never ran and
    # the HTTP runner was never cleaned up. A signal handler turns both TERM
    # and INT into a normal exit through `finally`.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
        logger.info("Shutdown signal received — stopping Brain cleanly.")
    finally:
        background = [t for t in (sweeper_task, retention_task, module_retry_task) if t]
        for task in background:
            task.cancel()
        # Await the cancellations so their cleanup actually runs before exit.
        await asyncio.gather(*background, return_exceptions=True)
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
