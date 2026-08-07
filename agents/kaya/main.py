# =============================================================================
# Кая entrypoint (Phase 3) — agents/kaya/main.py
# =============================================================================
# WHAT: Boots Кая. It:
#   1. ensures Кая's OWN database exists (creates it if not),
#   2. creates her tables straight from the models (metadata.create_all),
#   3. builds the Agent (soul + AnthropicClient + BrainMCPClient + DbHistory),
#   4. does the MCP handshake with Brain (fails fast if the token is bad),
#   5. polls Telegram, turning each message into agent.reply(text).
#
# WHY it creates its own DB (same as Brain): the Postgres container only auto-
#   creates the monolith's DB, so Кая creates hers from the maintenance DB.
#
# WHY the Brain handshake at boot: if KAYA_BRAIN_TOKEN is missing or wrong, we
#   want to know NOW (a loud failure at startup), not on the owner's first
#   message. This is the vertical-slice contract — Кая is only useful if she can
#   reach Brain for tools + memory.
#
# HOW it runs: `python -m agents.kaya.main` (the `kaya` service in
#   docker-compose). Кая = agents.core + this connector + her soul + her DB.
# =============================================================================

import asyncio
import logging
import sys
from pathlib import Path

import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.fsm.storage.memory import MemoryStorage

from aiohttp import web

from agents.core.agent import Agent
from agents.core.cli import ClaudeCliRunner
from agents.core.cliches import load_cliches, path_for_language as cliches_path_for_language
from agents.core.enroll import EnrollmentClient, FileCredentialStore
from agents.core.llm import AnthropicClient
from agents.core.locale import LanguageNotConfigured, require_language
from agents.core.loop import AgentLoop
from agents.core.mcp_client import BrainMCPClient, BrainMCPError
from agents.core.soul import load_soul
from agents.kaya.config import settings
from agents.kaya.connector import ChatTurns, build_router
from agents.kaya.dedup import SeenUpdatesMiddleware
from agents.kaya.delivery import build_delivery_app
from agents.kaya.history import DbHistory
from agents.kaya.stt import LANG_FOR_KAYA_LANGUAGE, SpeechKit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

KAYA_LOCALES_DIR = Path(__file__).with_name("locales")
CORE_LOCALES_DIR = Path(__file__).resolve().parent.parent / "core" / "locales"

# Every locale root Кая depends on, and the files each language must provide
# in it. Checked once at boot (main(), before anything else) so a
# half-translated KAYA_LANGUAGE fails loudly with the exact missing paths
# instead of degrading silently file-by-file — see agents/core/locale.py.
_LOCALE_ROOTS = {
    KAYA_LOCALES_DIR: ("soul.md", "strings.json"),
    CORE_LOCALES_DIR: ("cliches.json", "strings.json"),
}


def _soul_path() -> Path:
    return KAYA_LOCALES_DIR / settings.kaya_language / "soul.md"


def _load_persona() -> str:
    """soul + the shared cliché map (agents/core/locales/*/cliches.json) as
    one system-prompt block — the main turn and the self-check both see it."""
    cliches = load_cliches(cliches_path_for_language(settings.kaya_language))
    return load_soul(_soul_path()) + ("\n\n" + cliches if cliches else "")


def _gate_runner(token: str) -> ClaudeCliRunner | None:
    """A second CLI runner on a cheaper model for the self-check gate, or None
    to reuse the main one.

    WHY None is the default and the better choice: reusing the main runner lets
    the gate --resume the draft's own session, so the check costs one short
    message instead of a full replay of the transcript and system prompt. A
    separate model cannot resume that session, so setting CLAUDE_GATE_MODEL
    trades the resume saving for a cheaper per-token rate. Which wins depends
    on the traffic — the per-turn usage log is there to tell you."""
    if not settings.claude_gate_model:
        return None
    logger.info("Self-check gate on a separate model: %s", settings.claude_gate_model)
    return ClaudeCliRunner(
        model=settings.claude_gate_model,
        brain_mcp_url=settings.brain_url,
        agent_token=token,
        config_dir=settings.claude_config_dir,
        language=settings.kaya_language,
    )


async def ensure_database() -> None:
    """Create Кая's own database if it doesn't exist yet (idempotent)."""
    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_maintenance_db,
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", settings.kaya_postgres_db
        )
        if exists:
            logger.info("Кая database '%s' already exists.", settings.kaya_postgres_db)
            return
        await conn.execute(f'CREATE DATABASE "{settings.kaya_postgres_db}"')
        logger.info("Created Кая database '%s'.", settings.kaya_postgres_db)
    finally:
        await conn.close()


async def create_tables() -> None:
    """Bring Кая's database to the latest migration.

    Migrations, not create_all: create_all only ever creates MISSING TABLES, so
    a column added to a model after the database existed stayed missing until
    some query named it and the service 500'd.
    """
    from infra.migrations.runner import upgrade

    await upgrade("kaya")


def _require(condition: bool, message: str) -> None:
    """Fail fast at boot on a missing prerequisite (clearer than a later crash)."""
    if not condition:
        logger.error(message)
        sys.exit(1)


async def main() -> None:
    logger.info("Кая (Phase 3) starting...")
    try:
        require_language(settings.kaya_language, _LOCALE_ROOTS)
    except LanguageNotConfigured as e:
        _require(False, str(e))
    _require(bool(settings.telegram_bot_token), "TELEGRAM_BOT_TOKEN is empty.")
    _require(
        settings.claude_backend in ("api", "cli"),
        f"CLAUDE_BACKEND must be 'api' or 'cli', got '{settings.claude_backend}'.",
    )
    # The API key is required only for the API backend; the CLI backend uses the
    # Max login instead (and we strip the key so it doesn't fall back to billing).
    if settings.claude_backend == "api":
        _require(bool(settings.anthropic_api_key), "ANTHROPIC_API_KEY is empty (claude_backend=api).")
    # With an EMPTY whitelist the owner gate degrades to allow-all (see
    # OwnerOnlyMiddleware), which would open this single-owner bot — wired to the
    # owner's tools + memory via Brain — to any Telegram user who finds it. Refuse
    # to boot without an owner rather than run wide open.
    _require(
        bool(settings.allowed_user_ids),
        "ALLOWED_USER_IDS is empty — Кая would answer ANY Telegram user. Set the owner id(s).",
    )

    await ensure_database()
    await create_tables()
    purged = await DbHistory().purge_old()
    if purged:
        logger.info("Local history retention: deleted %d old message(s)", purged)

    # Connect to Brain — fully self-healing, zero token config needed:
    #   1. use an explicit KAYA_BRAIN_TOKEN if set, else the stored/enrolled one;
    #   2. if Brain rejects WHATEVER we present (stale token, reset DB), drop it
    #      and re-enroll — the owner's `y` at `make approve` is the only gate;
    #   3. only non-auth failures (Brain down/unreachable) abort the boot.
    cred_store = FileCredentialStore(settings.brain_token_file)
    enroller = EnrollmentClient(
        settings.brain_url, settings.agent_slug, settings.enroll_token, cred_store
    )

    async def handshake(tok: str) -> tuple[BrainMCPClient, dict]:
        client = BrainMCPClient(settings.brain_url, tok)
        return client, await client.initialize()

    # Try known credentials first: the STORED token (most recently issued —
    # takes priority) then an explicit KAYA_BRAIN_TOKEN as a fallback seed. A
    # stale entry in .env must never shadow a fresh enrolled token.
    brain = info = None
    for tok in [t for t in (cred_store.load(), settings.kaya_brain_token) if t]:
        try:
            brain, info = await handshake(tok)
            break
        except BrainMCPError as e:
            if "unauthorized" in str(e).lower():
                continue  # try the next candidate, else enroll below
            _require(False, f"Cannot reach Brain at {settings.brain_url}: {e}")
        except Exception as e:
            # Non-auth failure (connection refused, timeout): fail loudly.
            _require(False, f"Cannot reach Brain at {settings.brain_url}: {e}")

    if brain is None:
        # No working credential — (re-)pair: enroll and wait for the owner's `y`.
        logger.info("No valid Brain token — enrolling; approve with `make approve`.")
        cred_store.clear()
        try:
            brain, info = await handshake(await enroller.obtain_token())
        except Exception as e:
            _require(False, f"Pairing with Brain failed: {e}")
    logger.info("Brain handshake OK: %s", info.get("serverInfo"))
    token = brain.token  # the resolved token, for the CLI runner below

    # Pick the LLM backend: the Max/CLI runner, or the default API tool-loop.
    if settings.claude_backend == "cli":
        logger.info("LLM backend: claude CLI (Max login at %s)", settings.claude_config_dir)
        runner = ClaudeCliRunner(
            model=settings.claude_model,
            brain_mcp_url=settings.brain_url,
            # Use the RESOLVED token (explicit KAYA_BRAIN_TOKEN or the one obtained
            # via enrollment) — NOT settings.kaya_brain_token, which is empty on the
            # enrollment path, which would leave the CLI backend with no Brain access.
            agent_token=token,
            config_dir=settings.claude_config_dir,
            language=settings.kaya_language,
        )
        agent = Agent(
            soul=_load_persona(), brain=brain,
            history=DbHistory(window=settings.chat_context_messages),
            runner=runner, gate_runner=_gate_runner(token), timezone=settings.timezone,
        )
    else:
        logger.info("LLM backend: Anthropic API")
        gate_llm = (
            AnthropicClient(settings.anthropic_api_key, settings.claude_gate_model)
            if settings.claude_gate_model else None
        )
        agent = Agent(
            soul=_load_persona(), brain=brain,
            history=DbHistory(window=settings.chat_context_messages),
            llm=AnthropicClient(settings.anthropic_api_key, settings.claude_model),
            gate_runner=AgentLoop(gate_llm, brain) if gate_llm else None,
            timezone=settings.timezone,
        )

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    # Drop already-processed (redelivered) Telegram updates before any handler,
    # so a restart mid-message can't make Кая act on it twice.
    dp.update.outer_middleware(SeenUpdatesMiddleware())
    # Shared between the router and the delivery receiver so a self-reminder
    # wake-up can never run a turn while a live conversation is mid-turn.
    turns = ChatTurns()
    stt_lang = LANG_FOR_KAYA_LANGUAGE.get(settings.kaya_language, "ru-RU")
    dp.include_router(
        build_router(
            agent,
            settings.allowed_user_ids,
            SpeechKit(settings.yandex_stt_api_key, lang=stt_lang),
            turns,
        )
    )

    # Delivery / callbacks (Phase 6): register where Brain can push events to us,
    # and start the receiver that turns those pushes into Telegram messages.
    owner_id = settings.allowed_user_ids[0]
    if not settings.delivery_token:
        logger.warning(
            "DELIVERY_TOKEN is empty — the delivery receiver rejects every push, "
            "so reminders won't arrive. Set it (same value) on Brain and Кая."
        )
    try:
        await brain.register_delivery_addr(settings.delivery_public_addr)
        logger.info("Registered delivery address with Brain: %s", settings.delivery_public_addr)
    except (BrainMCPError, OSError) as e:
        # Non-fatal: Кая can still chat; only proactive pushes won't reach her.
        logger.warning("Could not register delivery address (%s) — pushes disabled.", e)

    delivery_runner = web.AppRunner(
        build_delivery_app(
            bot, owner_id, settings.delivery_token, agent=agent, turns=turns
        ),
        access_log=None,
    )
    await delivery_runner.setup()
    await web.TCPSite(
        delivery_runner, settings.delivery_bind_host, settings.delivery_port
    ).start()
    logger.info("Delivery receiver listening on :%d", settings.delivery_port)

    # Probe ONCE before entering the retry loop.
    #
    # WHY: Telegram permits exactly one long-poll consumer per bot token. Run the
    # stack on a laptop and on a server at the same time and both get
    # TelegramConflictError forever — neither wins, and aiogram's retry loop
    # (which is correct) turns it into an endless identical traceback whose text
    # never says what to DO. The condition is diagnosable in one call, so say the
    # actionable thing here, once, before the noise starts.
    try:
        await bot.get_updates(limit=1, timeout=0)
    except TelegramConflictError:
        logger.error(
            "Another instance is already polling THIS bot token (laptop? server?). "
            "Telegram allows exactly one — stop the other one, or give this "
            "environment its own token. Retrying anyway, but nothing will arrive "
            "until one of them stops."
        )
    except Exception as e:  # a network blip at boot is not worth failing over
        logger.warning("Could not probe Telegram before polling (%s) — continuing.", e)

    logger.info("Кая is live on Telegram (owner-only). Polling...")
    try:
        await dp.start_polling(bot)
    except TelegramConflictError:
        logger.error(
            "Polling stopped: the same bot token is being polled elsewhere. "
            "Only one instance may run per token."
        )
        raise
    finally:
        await delivery_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
