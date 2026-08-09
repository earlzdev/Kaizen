# =============================================================================
# Трекер module configuration — modules/tracker/config.py
# =============================================================================
# WHAT: Settings for the STANDALONE tracker module (Phase 9). It now owns its
#       data, so it has its own logical database, plus the admin token external
#       pollers/agents authenticate with, and two bind points: the gRPC Module
#       face (Brain) and the HTTP face (external project pollers + dashboard).
#
# WHY it became standalone (was an HTTP adapter over app/tracker): the v1
#       monolith is being deleted, so the tracker's store/models/API were ported
#       into this module with their OWN DB — no more dependency on app/*. Same
#       admin-token contract external pollers already speak (TRACKER_ADMIN_TOKEN),
#       so their claim/report keeps working unchanged.
#
# HOW: `from modules.tracker.config import settings`. In docker-compose the
#       `tracker` service reads .env, publishes the HTTP port, and binds gRPC.
# =============================================================================

from pydantic_settings import BaseSettings, SettingsConfigDict


class TrackerModuleSettings(BaseSettings):
    """The standalone tracker module's configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Admin credential: agents delegate/observe and the dashboard authenticate
    # with this; per-project tokens (minted on register) authenticate pollers.
    tracker_admin_token: str = ""

    # Its OWN logical database (projects, agents, tasks).
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "learnbot"
    postgres_password: str = "learnbot"
    tracker_postgres_db: str = "tracker"
    postgres_maintenance_db: str = "postgres"

    # gRPC Module face (Brain dials it) + HTTP face (external pollers + panel).
    tracker_module_bind_addr: str = "0.0.0.0:9103"
    tracker_module_name: str = "tracker"
    tracker_http_port: int = 8770

    # --- v2 Hub face (projects' Wardens dial it) --------------------------
    tracker_hub_bind_addr: str = "0.0.0.0:9104"

    # How long a lease lasts. Must comfortably exceed the Warden's heartbeat
    # interval (infra/wardenkit defaults to 30s) — set it too tight and a
    # single slow beat requeues healthy work; too loose and a dead project's
    # Directive sits stuck for that long before anyone notices.
    tracker_lease_seconds: int = 120

    # The Hub's hard ceiling on a held-open AskOwner, whatever the Warden asks
    # for. A crashed project must not be able to pin a server handler forever.
    tracker_question_max_sec: int = 1800
    # How often a waiting AskOwner re-reads its question row. The answer can
    # arrive through a different face of this service (Кая's tool, or the
    # panel), so the database is the only place all three can meet.
    tracker_question_poll_sec: int = 2

    # How often the dispatcher looks for queued work to hand out. Short: this
    # is the delay between the owner saying "do X" and the fleet starting.
    tracker_dispatch_interval_sec: int = 5
    # Deadline on one Dispatch/Health call. A Warden answers these immediately
    # by contract (it spawns the pipeline asynchronously), so a slow answer is
    # a sick project, not a busy one.
    tracker_dispatch_timeout_sec: int = 10
    # How many silent attempts before the owner is told a project is offline.
    # High enough that a container restart never generates a message.
    tracker_dispatch_max_attempts: int = 5

    # How often the sweeper looks for dead leases. Comfortably shorter than the
    # lease itself, so an expiry is noticed rather than merely recorded.
    tracker_sweep_interval_sec: int = 30

    # The human every project's fleet works for. Seeded onto each project's
    # roster as the top of its org chart — the architect reports to somebody,
    # and a chart that starts below them hides who that is.
    tracker_owner_name: str = "Owner"

    # Reaching the owner: the Hub POSTs its events to Brain, which pushes them
    # to the agent that talks to them (Кая). The tracker deliberately does NOT
    # know Кая's address or her delivery token — Brain owns that.
    brain_url: str = "http://brain:8772"
    module_event_token: str = ""

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL for the tracker's own database (asyncpg)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.tracker_postgres_db}"
        )


settings = TrackerModuleSettings()
