# =============================================================================
# Migration runner — infra/migrations/runner.py
# =============================================================================
# WHAT: Drives Alembic from Python instead of the CLI: `upgrade(service)` at
#       boot, `revision(service, msg)` when a model changes, plus a small
#       `python -m infra.migrations` entry point.
#
# WHY programmatic and not `alembic -c ...`: five services share ONE env.py but
#       need five separate version directories and five separate revision
#       chains. Alembic builds its ScriptDirectory from the ini BEFORE env.py
#       runs, so a `version_locations` set inside env.py arrives too late — the
#       first autogenerate lands in the shared root and every other service then
#       reports "target database is not up to date" against a revision that has
#       nothing to do with it. Setting it on a Config object here happens before
#       anything reads it, and the same call is what the services run at boot.
#
# WHY migrate at boot: `make up` has to be sufficient. The schema drifting away
#       from the models is exactly what bit this project in production — a
#       service that starts against a database it cannot use should fail loudly
#       at boot, not on the first request that names a new column.
#
# HOW:  await upgrade("brain")                       # in the service's startup
#       python -m infra.migrations upgrade brain     # by hand
#       python -m infra.migrations revision tracker "add purpose to project"
#       python -m infra.migrations upgrade --all
# =============================================================================

import asyncio
import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from infra.migrations import registry

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent


def _config(service: str) -> Config:
    """An Alembic Config pinned to one service's version directory."""
    if service not in registry.SERVICES:
        raise SystemExit(f"Unknown service {service!r}. "
                         f"Known: {', '.join(registry.SERVICES)}")
    cfg = Config(str(HERE / "alembic.ini"))
    cfg.set_main_option("script_location", str(HERE))
    cfg.set_main_option("version_locations", str(HERE / "versions" / service))
    # env.py reads this instead of the -x CLI argument when driven from here.
    cfg.set_main_option("service", service)
    cfg.cmd_opts = None
    return cfg


class _NoDatabase(Exception):
    """The server is up, but this service's database does not exist yet.

    Not an error: every service creates its OWN database on ITS first boot, so
    `upgrade --all` legitimately runs before some of them exist. Kept separate
    from "the server is down", which must still fail loudly.
    """


# Postgres SQLSTATE for "database does not exist".
_INVALID_CATALOG = "3D000"


def _adopt_if_legacy(service: str) -> None:
    """Stamp a database that predates migrations, instead of failing on it.

    These databases were built by `metadata.create_all`: their tables exist but
    there is no `alembic_version`, so a plain upgrade would replay the baseline
    and die on the first CREATE TABLE. Detecting that here turns the first
    deploy into a normal `make up` instead of a documented ritual nobody
    performs correctly at 3am.

    The stamp says "you already have the baseline". It does NOT say the database
    matches the models — that is what the drift-repair revision right after the
    baseline is for.
    """
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _tables() -> set[str]:
        # The ASYNC driver on purpose: asyncpg is the only Postgres driver these
        # images install, so building a sync engine here would raise
        # ModuleNotFoundError(psycopg2) on every boot.
        engine = create_async_engine(registry.url_of(service), poolclass=sa.pool.NullPool)
        try:
            async with engine.connect() as conn:
                return set(await conn.run_sync(
                    lambda sync_conn: sa.inspect(sync_conn).get_table_names()))
        except Exception as e:
            # ONLY "no such database" is tolerated — a down server, bad
            # credentials or anything else must still blow up.
            if getattr(getattr(e, "orig", None), "sqlstate", None) == _INVALID_CATALOG:
                raise _NoDatabase(service) from e
            raise
        finally:
            await engine.dispose()

    tables = asyncio.run(_tables())
    if "alembic_version" in tables:
        return                          # already under migration control
    if not (tables & set(registry.metadata_of(service).tables)):
        return                          # genuinely empty: let the baseline run
    logger.warning(
        "%s has tables but no migration history — adopting it: stamping the "
        "baseline, then letting the drift-repair revision reconcile it.",
        service,
    )
    cfg = _config(service)
    from alembic.script import ScriptDirectory

    base_rev = ScriptDirectory.from_config(cfg).get_base()
    command.stamp(cfg, base_rev)


def upgrade_sync(service: str) -> None:
    """Bring one service's database to head. Blocking — see `upgrade` for async.

    Alembic is synchronous by design; env.py opens its own async engine inside.

    A service whose database does not exist yet is SKIPPED, not failed: each
    service creates its own database on its own first boot, and one that has
    never been started (today: kuzya, which has no compose service yet) would
    otherwise abort `upgrade --all` — taking every service after it in the loop
    down with it, silently un-migrated.
    """
    try:
        _adopt_if_legacy(service)
    except _NoDatabase:
        logger.warning(
            "%s: no database yet — skipping. It will migrate itself when that "
            "service first boots and creates it.", service,
        )
        return
    logger.info("Migrating %s to head…", service)
    command.upgrade(_config(service), "head")
    logger.info("%s schema is at head.", service)


async def upgrade(service: str) -> None:
    """Async wrapper for a service's startup path.

    Runs in a worker thread: Alembic's own engine drives an event loop of its
    own, and nesting that inside the caller's loop deadlocks.
    """
    await asyncio.to_thread(upgrade_sync, service)


def revision(service: str, message: str, autogenerate: bool = True) -> None:
    """Write a new revision by diffing the models against the live database."""
    command.revision(_config(service), message=message, autogenerate=autogenerate)


def stamp(service: str, target: str = "head") -> None:
    """Mark a database as already at a revision, without running it.

    The one-time handshake for a database that predates migrations: its tables
    already exist, so replaying the baseline would fail on every CREATE TABLE.
    """
    command.stamp(_config(service), target)


def current(service: str) -> None:
    command.current(_config(service), verbose=True)


def history(service: str) -> None:
    command.history(_config(service), verbose=False)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__ or "", file=sys.stderr)
        print("usage: python -m infra.migrations "
              "{upgrade|revision|stamp|current|history} <service|--all> [message]",
              file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s %(message)s")
    action, rest = argv[0], argv[1:]
    services = (list(registry.SERVICES) if rest and rest[0] == "--all"
                else rest[:1])
    if not services:
        print("Which service? (or --all)", file=sys.stderr)
        return 2
    extra = rest[1:]
    for service in services:
        if action == "upgrade":
            upgrade_sync(service)
        elif action == "revision":
            if not extra:
                print("A revision needs a message.", file=sys.stderr)
                return 2
            revision(service, " ".join(extra))
        elif action == "stamp":
            stamp(service, extra[0] if extra else "head")
        elif action == "current":
            print(f"--- {service}")
            current(service)
        elif action == "history":
            print(f"--- {service}")
            history(service)
        else:
            print(f"Unknown action {action!r}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
