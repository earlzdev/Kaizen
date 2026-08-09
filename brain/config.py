# =============================================================================
# Brain configuration — brain/config.py
# =============================================================================
# WHAT: Brain's own settings, separate from app/config.py. Chiefly its database
#       URL, which points at Brain's OWN logical database inside the shared
#       Postgres instance.
#
# WHY a separate config (and a separate DB name): the plan mandates
#   "Postgres — one instance, one DB per service" — every service has its own logical
#   database, no cross-service JOINs. Brain must therefore build its URL from
#   its OWN db name (BRAIN_POSTGRES_DB, default "brain"), while still sharing the
#   host/user/password of the single Postgres container. Reusing app.config's
#   `database_url` would point Brain at the v1 monolith's database — exactly the
#   coupling the plan forbids.
#
# WHY it still reads the shared POSTGRES_* vars: there is one Postgres container
#   with one superuser; only the target database differs per service. So Brain
#   takes host/port/user/password from the same env the monolith uses, and only
#   overrides the database name.
#
# HOW: `from brain.config import settings`. In docker-compose the `brain`
#   service sets POSTGRES_HOST=postgres; BRAIN_POSTGRES_DB defaults to "brain".
# =============================================================================

from pydantic_settings import BaseSettings, SettingsConfigDict


class BrainSettings(BaseSettings):
    """Brain's configuration, validated at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Shared Postgres container (same instance the monolith uses)...
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "learnbot"
    postgres_password: str = "learnbot"
    # ...but Brain's OWN logical database. This is the one field that isolates
    # Brain's schema from the v1 monolith's.
    brain_postgres_db: str = "brain"

    # Modules Brain discovers + proxies tools for. Format:
    #   "name=host:port,name2=host:port"  (empty = built-in memory tools only).
    # On startup Brain calls each module's RegisterTools and merges its tools
    # into the registry; a module tool then behaves like a built-in over MCP.
    brain_modules: str = ""

    # Shared secret that gates agent ENROLLMENT requests (device pairing). All
    # your agents know it; it stops randoms from creating pending requests. The
    # owner still approves each one. Empty = enrollment disabled (rejects all).
    enroll_token: str = ""

    # MCP front door (Phase 2). Binds 0.0.0.0; every route needs a token.
    brain_port: int = 8772
    # Credential for the /admin routes that mint agents. Empty = /admin rejects
    # everything (no agents can be minted) — Brain warns loudly at boot.
    brain_admin_token: str = ""

    # The maintenance database Brain connects to in order to CREATE its own DB
    # if it doesn't exist yet. "postgres" always exists in a stock cluster.
    postgres_maintenance_db: str = "postgres"

    # Delivery / callbacks (Phase 6). Brain pushes events (a fired reminder) to
    # an agent's delivery_addr. The shared secret authenticates Brain -> agent
    # pushes; the sweeper interval is how often Brain checks for due reminders.
    delivery_token: str = ""
    reminder_sweep_seconds: int = 30
    delivery_timeout: float = 10.0

    # Shared secret a MODULE presents to POST /event — the one route where a
    # module reaches an agent instead of the other way round (the tracker Hub
    # telling the owner a PR is ready, or that a project's agent has a
    # question). Deliberately NOT brain_admin_token: this grants exactly "push
    # a message to an agent", nothing the admin token grants. Empty = /event
    # rejects everything, so a missing secret fails closed.
    module_event_token: str = ""

    # How often Brain retries RegisterTools for a configured module that was
    # unreachable (still booting, crashed) — until it registers. Without this,
    # a module that came up slower than Brain lost its tools until a Brain
    # restart (ARCHITECTURE_REVIEW.md §2.2-3).
    module_retry_seconds: int = 60

    # Cosine distance below which a new fact REFRESHES an existing one instead
    # of being stored alongside it. Deliberately tight (Step 9): at the old
    # 0.15, "pasha loves coffee" could silently overwrite "masha loves coffee" —
    # only true rephrasings should merge; when unsure, keep both.
    memory_duplicate_threshold: float = 0.05

    # Where a reminder goes when its OWNING agent has no delivery address:
    # the agent with this slug (compose sets "kaya"). Empty = no fallback —
    # the reminder is skipped with a warning until the owner reconnects.
    # Replaces the old "any agent with an address" pick (Step 9), which would
    # misroute reminders the moment a second agent (Кузя) registers one.
    default_delivery_agent_slug: str = ""

    # Last-resort timezone for a reminder set with a naive time when the owner
    # has no profile timezone. IANA name. Better to set the owner's profile.
    default_timezone: str = "UTC"

    # Backup service (the admin panel proxies list/create through Brain to it).
    backup_url: str = "http://backup:8781"
    backup_api_token: str = ""  # override; defaults to brain_admin_token below

    # Tracker Hub's HTTP API (the mobile dashboard proxies read-only calls
    # through Brain to it, same shape as the backup proxy above). Brain never
    # touches tracker's DB — this is a plain HTTP client credential, not a
    # service import, so it doesn't violate DB-per-service isolation.
    tracker_http_url: str = "http://tracker:8770"
    tracker_admin_token: str = ""

    @property
    def backup_token(self) -> str:
        """Token Brain uses to call the backup API (dedicated override, else the
        admin token — which is the backup service's default too)."""
        return self.backup_api_token or self.brain_admin_token

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL for Brain's own database (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.brain_postgres_db}"
        )


# Singleton — import `settings` instead of constructing new instances.
settings = BrainSettings()
