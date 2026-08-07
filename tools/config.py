# =============================================================================
# Tools service configuration — tools/config.py
# =============================================================================
# WHAT: Settings the utility tools need — Travelpayouts credentials (flights),
#       the headless-browser toggle/timeout — plus this service's gRPC bind.
#
# WHY it reuses the same env names as before (TRAVELPAYOUTS_TOKEN, BROWSER_*):
#       the tool services were lifted from the v1 monolith and read these fields;
#       keeping the names means the existing .env keeps working.
#
# HOW: `from tools.config import settings`. In docker-compose the `tools`
#       service reads .env and binds 0.0.0.0:9105.
# =============================================================================

from pydantic_settings import BaseSettings, SettingsConfigDict


class ToolsSettings(BaseSettings):
    """The tools service's configuration, validated at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Flights (Travelpayouts API).
    travelpayouts_token: str = ""
    travelpayouts_marker: str = ""

    # Headless-browser rendering (Playwright/Chromium). Heavy; can be disabled.
    browser_enabled: bool = True
    browser_timeout_seconds: int = 30

    # gRPC bind address (this service is the server; Brain dials it).
    tools_bind_addr: str = "0.0.0.0:9105"
    tools_module_name: str = "tools"


settings = ToolsSettings()
