# =============================================================================
# Which service owns which schema — infra/migrations/registry.py
# =============================================================================
# WHAT: The one table mapping a service name to its SQLAlchemy metadata, its
#       database URL, and the extensions its schema needs before any table.
#
# WHY here and not five copies: every service keeps DB-per-service isolation
#       (its own database, its own metadata, its own `alembic_version` row), but
#       the RUNNER is shared infrastructure, exactly like infra/proto. Five
#       env.py files would be five places to fix the next time the async engine
#       setup changes.
#
# WHY lazy imports: a container runs ONE service and only installs that
#       service's dependencies. Importing all eagerly would make every
#       service fail on somebody else's import.
#
# HOW: `metadata_of("brain")` / `url_of("brain")`. Add a service by adding one
#      entry here and a versions/<service>/ directory.
# =============================================================================

from importlib import import_module

# service -> (models module, metadata attr, config module, how to get settings,
#             extensions).
_SERVICES = {
    "brain": ("brain.db.models", "Base", "brain.config",
              lambda m: m.settings, ("vector",)),
    "kaya": ("agents.kaya.db.models", "Base", "agents.kaya.config",
             lambda m: m.settings, ()),
}

SERVICES = tuple(_SERVICES)


def metadata_of(service: str):
    """The service's SQLAlchemy MetaData — what migrations are diffed against."""
    models_mod, base_attr, _, _, _ = _SERVICES[service]
    return getattr(import_module(models_mod), base_attr).metadata


def url_of(service: str) -> str:
    """The service's database URL, from its own settings — never a shared one.

    Read at call time, not import time: the URL comes from the environment, and
    a module-level read would bind the value to whenever this file happened to
    be imported.
    """
    _, _, config_mod, get_settings, _ = _SERVICES[service]
    return get_settings(import_module(config_mod)).database_url


def extensions_of(service: str) -> tuple[str, ...]:
    """Postgres extensions that must exist before this service's tables.

    `vector` for anything holding embeddings: the column type does not exist
    until the extension does, so this runs ahead of the first migration rather
    than inside one.
    """
    _, _, _, _, extensions = _SERVICES[service]
    return extensions
