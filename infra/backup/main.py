# =============================================================================
# Backup service entrypoint — backup/main.py
# =============================================================================
# WHAT: Runs scheduled backups AND serves a tiny internal API the admin panel
#       drives: GET /backups (list), POST /backups (run now), GET /health
#       (reports whether backups are configured AND when one last succeeded).
#       At boot it checks the newest backup in S3 and runs a CATCH-UP backup if
#       it is overdue — a container that restarts more often than the schedule
#       interval used to silently NEVER back up (Step 8 of
#       ARCHITECTURE_REVIEW.md).
#
# WHY the blocking work runs in a thread (asyncio.to_thread): create_backup and
#       the S3 calls are blocking (subprocess + boto3); offloading them keeps the
#       scheduler + API responsive and prevents two backups overlapping via a lock.
#
# WHY token-auth on an internal-only service: it publishes no host port (only
#       Brain reaches it on the compose network), but requiring the token is
#       cheap defense in depth. No token configured → the API refuses (fail-safe).
#
# HOW it runs: `python -m backup.main` (the `backup` service in docker-compose).
# =============================================================================

import asyncio
import datetime
import logging

from aiohttp import web

from infra.backup import core
from infra.backup.config import settings

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

_lock = asyncio.Lock()  # never run two backups at once

# Health state: when a backup last SUCCEEDED / last failed (this process).
# In-memory on purpose — /health is a liveness signal, not an audit trail
# (the durable record is the S3 listing itself).
_state: dict = {"last_success": None, "last_error": None}


def _authed(request: web.Request) -> bool:
    token = settings.api_token
    header = request.headers.get("Authorization", "")
    got = header[len("Bearer ") :].strip() if header.startswith("Bearer ") else ""
    return bool(token) and got == token


async def _run_backup() -> str:
    async with _lock:
        try:
            key = await asyncio.to_thread(core.create_backup)
        except Exception as e:
            _state["last_error"] = f"{datetime.datetime.now(datetime.timezone.utc).isoformat()}: {e}"
            raise
        _state["last_success"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return key


def _newest_backup_age_hours() -> float | None:
    """Hours since the newest backup in S3, or None when there is none.
    Sync (boto3) — call via to_thread."""
    items = core.list_backups()
    if not items:
        return None
    newest = datetime.datetime.fromisoformat(items[0]["last_modified"])
    return (datetime.datetime.now(datetime.timezone.utc) - newest).total_seconds() / 3600


async def _catch_up_if_overdue() -> None:
    """Boot catch-up: back up NOW if the newest backup is older than one
    schedule interval (or there is none). Errors are logged, never fatal —
    a misconfigured S3 must not crash-loop the service."""
    try:
        age = await asyncio.to_thread(_newest_backup_age_hours)
    except Exception:
        logger.exception("Could not check backup freshness; skipping boot catch-up")
        return
    if age is not None and age < settings.backup_schedule_hours:
        logger.info("Newest backup is %.1fh old (< %.0fh) — no catch-up needed.",
                    age, settings.backup_schedule_hours)
        return
    logger.info(
        "Backups are overdue (%s) — running a boot catch-up backup.",
        "none exist yet" if age is None else f"newest is {age:.1f}h old",
    )
    try:
        key = await _run_backup()
        logger.info("Boot catch-up backup done: %s", key)
    except Exception:
        logger.exception("Boot catch-up backup failed; the schedule will retry")


async def handle_list(request: web.Request) -> web.Response:
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        return web.json_response({"backups": await asyncio.to_thread(core.list_backups)})
    except Exception as e:
        logger.exception("list backups failed")
        return web.json_response({"error": str(e)}, status=502)


async def handle_create(request: web.Request) -> web.Response:
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    if _lock.locked():
        return web.json_response({"error": "a backup is already running"}, status=409)
    try:
        key = await _run_backup()
        return web.json_response({"ok": True, "key": key}, status=201)
    except Exception as e:
        logger.exception("backup failed")
        return web.json_response({"error": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    # last_success/last_error make "the service is up but hasn't backed up in
    # a week" VISIBLE — "configured" alone hid exactly that failure mode.
    return web.json_response(
        {
            "status": "ok",
            "configured": settings.configured,
            "last_success": _state["last_success"],
            "last_error": _state["last_error"],
        }
    )


async def scheduler() -> None:
    """Boot catch-up if overdue, then a backup every backup_schedule_hours.
    Errors are logged, never fatal."""
    if settings.configured:
        await _catch_up_if_overdue()
    interval = max(0.1, settings.backup_schedule_hours) * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            key = await _run_backup()
            logger.info("Scheduled backup done: %s", key)
        except Exception:
            logger.exception("Scheduled backup failed; will retry next interval")


async def main() -> None:
    logger.info("Backup service starting (configured=%s)", settings.configured)
    if not settings.configured:
        logger.warning("Backup NOT configured — set BACKUP_S3_* + BACKUP_AGE_RECIPIENT in .env.")
    if not settings.api_token:
        logger.warning("No API token — the /backups API will reject every request.")

    app = web.Application()
    app.router.add_get("/backups", handle_list)
    app.router.add_post("/backups", handle_create)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, settings.backup_bind_host, settings.backup_port).start()
    logger.info("Backup API on :%d", settings.backup_port)

    task = asyncio.create_task(scheduler())
    try:
        await asyncio.Event().wait()
    finally:
        task.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
