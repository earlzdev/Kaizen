# =============================================================================
# Additive drift repair — infra/migrations/drift.py
# =============================================================================
# WHAT: Adds columns the models declare and the database lacks. Used by the
#       one-time "adopt an existing database" revision, and available to any
#       later revision that has to reconcile a database nobody migrated.
#
# WHY it exists at all: these databases predate migrations. They were built by
#       `metadata.create_all`, which creates missing TABLES and never touches an
#       existing one, so they carry an unknown set of missing columns — `purpose`
#       on tracker_project is simply the one that got noticed, by 500ing in
#       production. Stamping such a database at the baseline would declare it
#       up to date while it still cannot serve a query.
#
# WHY additive only, forever: dropping a column that the models no longer
#       mention would let a stale checkout delete production data. Removals stay
#       a human decision, written as an explicit revision.
#
# WHY it skips NOT NULL without a default: adding one to a table with rows is
#       rejected by Postgres, and inventing a backfill value is a product
#       decision. It is reported instead of guessed.
# =============================================================================

import logging

from sqlalchemy import inspect

logger = logging.getLogger("alembic.drift")


def relax_nullability(connection, metadata) -> list[str]:
    """Drop NOT NULL where the models say a column is optional.

    The other half of pre-migration drift, and the half nobody sees coming: the
    database is STRICTER than the code. `tracker_projects.token` is the worked
    example — it was NOT NULL when every project got its token at creation, the
    model later made it optional (a project sits `pending` until the owner
    approves it, and has no token until then), and `create_all` cannot alter a
    constraint. Enrollment then fails on an insert that is perfectly valid
    according to the models.

    Only ever LOOSENS. Adding NOT NULL to a column that has nulls fails, and
    deciding what to backfill is a product decision, not a migration's.
    """
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    applied: list[str] = []

    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        live = {c["name"]: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            info = live.get(column.name)
            if info is None or column.primary_key:
                continue
            if column.nullable and not info["nullable"]:
                ddl = (f'ALTER TABLE "{table.name}" '
                       f'ALTER COLUMN "{column.name}" DROP NOT NULL')
                connection.exec_driver_sql(ddl)
                applied.append(ddl)
                logger.info("relaxed NOT NULL: %s.%s", table.name, column.name)

    if not applied:
        logger.info("no nullability drift")
    return applied


def add_missing_columns(connection, metadata) -> list[str]:
    """Add every column the models declare and this database does not have.

    Returns the DDL it ran, so a revision can log exactly what it repaired.
    Runs on the connection Alembic already holds — inside the migration's
    transaction, so a failure rolls the whole revision back.
    """
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    applied: list[str] = []

    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            # A table this database has never had is the baseline's job, not
            # ours: it was either just created by an earlier revision, or this
            # database is fresh and there is nothing to reconcile.
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            if not column.nullable and column.default is None \
                    and column.server_default is None:
                logger.warning(
                    "%s.%s is NOT NULL with no default — needs a human decision "
                    "about the value for existing rows; skipped",
                    table.name, column.name,
                )
                continue
            type_sql = column.type.compile(connection.dialect)
            null_sql = "" if column.nullable else " NOT NULL"
            default_sql = ""
            if column.server_default is not None:
                default_sql = f" DEFAULT {column.server_default.arg}"
            ddl = (f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS '
                   f'"{column.name}" {type_sql}{default_sql}{null_sql}')
            connection.exec_driver_sql(ddl)
            applied.append(ddl)
            logger.info("drift repaired: %s.%s", table.name, column.name)

    if not applied:
        logger.info("no drift: every declared column already exists")
    return applied
