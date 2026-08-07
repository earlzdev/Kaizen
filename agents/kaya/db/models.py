# =============================================================================
# Кая local schema — agents/kaya/db/models.py
# =============================================================================
# WHAT: Кая's local database schema — a single `messages` table holding her
#       conversation with the owner (role + text + time).
#
# WHY just one table (no user_id): Кая serves ONE owner, so there is no per-user
#       routing. She stores only the dialogue; everything durable about the owner
#       (facts, profile, reminders) lives in Brain's shared memory, not here.
#       This is the "локальная память (история диалога, своя БД агента)" from
#       the plan — deliberately minimal.
#
# WHY the full history is kept (only a window is loaded): storing everything is
#       cheap and useful for export/debugging; the prompt only ever loads the
#       newest N (chat_context_messages), so the context never grows.
#
# HOW: this Base's metadata is Кая's Alembic target (agents/kaya/migrations).
# =============================================================================

import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for Кая's local schema."""


class Message(Base):
    """One turn of Кая's conversation with the owner."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SeenUpdate(Base):
    """A Telegram update_id Кая has already processed.

    WHY: if Кая restarts BEFORE aiogram acks an update, Telegram redelivers it
    on reconnect — and she'd act on the same message twice (e.g. create a second
    reminder at a shifted time). Recording each processed update_id and skipping
    repeats makes handling idempotent across restarts. update_id is Telegram's
    own id, so it IS the primary key (not autoincremented)."""

    __tablename__ = "seen_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
