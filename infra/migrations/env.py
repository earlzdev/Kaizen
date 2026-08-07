# =============================================================================
# Alembic environment — infra/migrations/env.py
# =============================================================================
# WHAT: The single Alembic env shared by every service. Which service it runs
#       for comes from `-x service=<name>` (CLI) or the SERVICE env var, and
#       that choice picks the metadata, the database URL and the versions dir.
#
# WHY one env for five services: they keep separate databases and separate
#       `alembic_version` tables — the isolation that matters — but the runner
#       is shared infrastructure. Five copies of this file would be five places
#       to fix the next async-engine papercut.
#
# WHY the extensions run before the migration: a `Vector` column cannot be
#       created until `CREATE EXTENSION vector` has run, and putting that inside
#       the first migration makes every fresh database depend on the ORDER of a
#       revision chain someone may later squash.
#
# HOW:  alembic -c infra/migrations/alembic.ini -x service=brain upgrade head
#       (or `make migrate-up`, which loops over every service).
# =============================================================================

import asyncio
import os

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from infra.migrations import registry

config = context.config


def _service() -> str:
    """Which service this run is for. Explicit — never a default.

    Three sources because there are three callers: the runner (which sets a
    `service` main option before Alembic reads anything), the bare CLI (`-x
    service=…`), and an operator with an env var.
    """
    name = (config.get_main_option("service", None)
            or context.get_x_argument(as_dictionary=True).get("service")
            or os.environ.get("SERVICE") or "").strip()
    if name not in registry.SERVICES:
        raise SystemExit(
            f"Pass a service: -x service=<{'|'.join(registry.SERVICES)}> "
            f"(got {name!r})"
        )
    return name


SERVICE = _service()
target_metadata = registry.metadata_of(SERVICE)
config.set_main_option("sqlalchemy.url", registry.url_of(SERVICE))
# NOTE: `version_locations` is deliberately NOT set here — Alembic builds its
# ScriptDirectory before env.py runs, so it would be ignored. runner.py sets it
# on the Config object instead.


def _render_item(type_, obj, autogen_context):
    """Make autogenerate emit an IMPORT for third-party column types.

    Alembic renders `pgvector.sqlalchemy.vector.VECTOR(dim=384)` into the
    migration but has no idea that name needs importing, so the file it writes
    raises NameError the first time anyone runs it — on a fresh database, i.e.
    exactly when nobody is watching. Registering the import here fixes it at the
    source instead of by hand-editing each generated file.
    """
    if type_ == "type" and obj.__class__.__module__.startswith("pgvector"):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        return f"pgvector.sqlalchemy.Vector(dim={getattr(obj, 'dim', None)})"
    return False


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_item=_render_item,
        # Type changes are the ones autogenerate is worst at guessing, and the
        # ones that rewrite a table. Comparing them means they show up in the
        # diff for a human to accept or reject, instead of silently never
        # appearing until something reads a column that changed shape.
        compare_type=True,
        compare_server_default=True,
    )


def run_migrations_offline() -> None:
    """Emit SQL without a database — `--sql` mode, for review before applying."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        for extension in registry.extensions_of(SERVICE):
            await connection.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{extension}"'))
            await connection.commit()
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
