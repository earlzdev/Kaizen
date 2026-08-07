# =============================================================================
# Кая update dedup — agents/kaya/dedup.py
# =============================================================================
# WHAT: An aiogram middleware that drops Telegram updates Кая has already
#       processed, so a message is never handled twice.
#
# WHY: aiogram acks the update offset to Telegram AFTER handling. If Кая crashes
#       or is restarted between handling and acking, Telegram REDELIVERS the
#       update on reconnect — and she'd re-run the handler (e.g. create a second
#       reminder at a shifted time). Recording each processed update_id and
#       skipping repeats makes handling idempotent across restarts.
#
# WHY at the UPDATE level (dp.update.outer_middleware): it must run before ANY
#       routing/handler, on the raw Update (which carries update_id). An
#       INSERT ... ON CONFLICT DO NOTHING is the atomic "claim this update": a
#       fresh id inserts (rowcount 1 -> process); a repeat conflicts (rowcount 0
#       -> skip). Concurrency-safe even if two deliveries race.
#
# HOW: `dp.update.outer_middleware(SeenUpdatesMiddleware())` in main.py.
# =============================================================================

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from agents.kaya.db.models import SeenUpdate
from agents.kaya.db.session import get_session

logger = logging.getLogger(__name__)


async def _claim(update_id: int, factory: async_sessionmaker | None = None) -> bool:
    """Record update_id; return True if it's newly claimed (process it), False
    if it was already seen (a redelivery — skip)."""
    async with get_session(factory) as session:
        result = await session.execute(
            pg_insert(SeenUpdate)
            .values(update_id=update_id)
            .on_conflict_do_nothing(index_elements=["update_id"])
        )
        return result.rowcount > 0


class SeenUpdatesMiddleware(BaseMiddleware):
    """Skips already-processed Telegram updates (idempotent across restarts)."""

    def __init__(self, session_factory: async_sessionmaker | None = None) -> None:
        # None = Кая's default DB; tests pass a scratch-DB sessionmaker.
        self._sessions = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            fresh = await _claim(event.update_id, self._sessions)
            if not fresh:
                logger.info("Skipping redelivered update %d", event.update_id)
                return None
        return await handler(event, data)
