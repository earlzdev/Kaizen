# =============================================================================
# Кая configuration — agents/kaya/config.py
# =============================================================================
# WHAT: Кая's settings — her Telegram token, her Claude credentials/model, the
#       Brain URL + her Brain token, the owner whitelist, and her OWN local
#       database (conversation history).
#
# WHY her own logical DB (kaya_postgres_db, default "kaya"): the plan gives each
#       agent its own local DB for dialogue history, separate from Brain's SHARED
#       memory. She reuses the one Postgres container (host/user/password) and
#       only overrides the database name — same isolation approach as Brain.
#
# WHY a Brain token here: Кая authenticates to Brain over MCP with her own agent
#       token (minted via Brain's /admin/agents). It scopes what she may do via
#       the access-list. Without it she cannot reach tools or memory.
#
# HOW: `from agents.kaya.config import settings`. In docker-compose the `kaya`
#       service sets POSTGRES_HOST=postgres and BRAIN_URL=http://brain:8772.
# =============================================================================

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class KayaSettings(BaseSettings):
    """Кая's configuration, validated at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    telegram_bot_token: str = ""
    # Only these Telegram ids may talk to Кая (single-owner bot).
    allowed_user_ids: list[int] = []

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def parse_allowed_ids(cls, v):
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        return v

    # Claude (Anthropic SDK lives in agents.core; these feed AnthropicClient).
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-4-8"
    # Optional cheaper model for the self-check gate (agent.py _final_gate).
    # The gate fires on most non-trivial replies and is deletion-only work —
    # spot a cliché, cut it — so it does not need the main model. Empty (the
    # default) reuses the main runner, which also lets the gate RESUME the
    # draft's session instead of replaying it; setting this trades that saving
    # for a cheaper model, so measure both before committing to one.
    claude_gate_model: str = ""
    # LLM backend: "api" (Anthropic API, per-token) or "cli" (the `claude` CLI
    # logged in with a Max subscription — no per-token billing). CLI reaches
    # Brain's tools over MCP itself; it needs a persisted login (below).
    claude_backend: str = "api"
    # Where the `claude` CLI keeps its login. For the cli backend, mount a volume
    # here so the login survives container recreation, then run once:
    #   docker compose exec kaya claude   (follow the login prompts)
    claude_config_dir: str = "/root/.claude"

    # Brain (shared tools + memory) over MCP.
    brain_url: str = "http://brain:8772"
    # Explicit token (optional). If empty, Кая ENROLLS: she asks Brain to connect,
    # you approve in the terminal, and she stores the issued token in the file
    # below (so no token in .env, and she reconnects automatically after that).
    kaya_brain_token: str = ""
    agent_slug: str = "kaya"
    enroll_token: str = ""
    # Where the enrolled token is persisted (mount a volume here so it survives).
    brain_token_file: str = "/state/brain_token"

    # Delivery / callbacks (Phase 6). Кая runs a small receiver that Brain POSTs
    # events to (a fired reminder); she then messages the owner in Telegram.
    #   delivery_token       — shared secret; must equal Brain's DELIVERY_TOKEN.
    #   delivery_bind_host/port — where the receiver listens inside the container.
    #   delivery_public_addr — the URL Brain uses to reach it (compose DNS). Кая
    #                          registers this with Brain at boot.
    delivery_token: str = ""
    delivery_bind_host: str = "0.0.0.0"
    delivery_port: int = 8780
    delivery_public_addr: str = "http://kaya:8780/deliver"

    # Yandex SpeechKit API key — transcribes the owner's Telegram voice messages
    # (empty = voice messages politely declined).
    yandex_stt_api_key: str = ""

    # Which locales/<lang>/ variant to load — picks her soul, cliché map, and
    # every user-facing string (see agents/README.md's "Multi-language
    # support"). Only "ru" and "en" are fully translated today; anything else
    # makes her refuse to boot and log the exact missing files.
    kaya_language: str = "en"

    # Timezone for resolving "tomorrow at 9" etc. in Кая's system prompt.
    timezone: str = "Europe/Moscow"
    # How many newest turns of local history to load per reply (fixed window).
    # Every turn re-sends this whole window, so it is a direct multiplier on
    # cost. 16 rather than the original 30 because anything older is reachable
    # on demand anyway — the exchange archive plus search_conversations reach
    # back further than the window ever did, and recall_memory covers the facts.
    chat_context_messages: int = 16

    # Кая's own local database (dialogue history).
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "learnbot"
    postgres_password: str = "learnbot"
    kaya_postgres_db: str = "kaya"
    postgres_maintenance_db: str = "postgres"

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL for Кая's own database (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.kaya_postgres_db}"
        )


settings = KayaSettings()
