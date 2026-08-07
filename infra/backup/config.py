# =============================================================================
# Backup configuration — backup/config.py
# =============================================================================
# WHAT: Settings for the backup service: how to reach Postgres (for pg_dumpall),
#       the Yandex S3 target, the age recipient (public key), schedule/retention,
#       and the API token the admin panel authenticates with.
#
# WHY it reuses BRAIN_ADMIN_TOKEN as the default API token: the admin panel
#       already holds that token; the backup API is internal-only (no published
#       port), so reusing it avoids minting yet another secret. Override with
#       BACKUP_API_TOKEN if you want them separate.
#
# HOW: `from infra.backup.config import settings`.
# =============================================================================

from pydantic_settings import BaseSettings, SettingsConfigDict


class BackupSettings(BaseSettings):
    """The backup service's configuration, validated at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres to dump (whole cluster). Connect as the superuser.
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "learnbot"
    postgres_password: str = "learnbot"

    # Yandex Object Storage (S3-compatible).
    backup_s3_endpoint: str = "https://storage.yandexcloud.net"
    backup_s3_region: str = "ru-central1"
    backup_s3_bucket: str = ""
    backup_s3_prefix: str = "kaizen"
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""

    # age recipient (PUBLIC key). Encryption only; the private key stays offline.
    backup_age_recipient: str = ""

    # Schedule + retention.
    backup_schedule_hours: float = 24.0
    backup_keep: int = 30

    # Internal API bind + auth (no published port; only Brain reaches it).
    backup_bind_host: str = "0.0.0.0"
    backup_port: int = 8781
    brain_admin_token: str = ""          # default auth token (shared with the panel)
    backup_api_token: str = ""           # optional dedicated override

    @property
    def api_token(self) -> str:
        """The token the API requires (dedicated override, else the admin token)."""
        return self.backup_api_token or self.brain_admin_token

    @property
    def configured(self) -> bool:
        """True only if S3 + age are set — otherwise backups can't run."""
        return bool(
            self.backup_s3_bucket
            and self.backup_s3_access_key
            and self.backup_s3_secret_key
            and self.backup_age_recipient
        )


settings = BackupSettings()
