# =============================================================================
# Brain provenance — brain/provenance.py
# =============================================================================
# WHAT: Records which agent made each write to Brain's shared memory, and
#       appends an audit row (change_log) for it. The acting agent travels
#       through a ContextVar so the store layer never threads an agent_id
#       through every call.
#
# WHY a ContextVar (lifted from app/core/provenance.py): the MCP server sets the
#       acting agent once per request from the bearer token; the memory store
#       reads it exactly where it writes. Async-safe — a ContextVar is isolated
#       per task, so concurrent agents never see each other's actor.
#
# WHY it degrades to a no-op: if no actor is set (e.g. a system/seed write),
#       record_change does nothing and rows get a null agent_id. Same
#       incremental-migration property as the v1 design.
# =============================================================================

from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession

from brain.db.models import ChangeLog

# The agent acting on the current request, or None for a system write.
current_actor_id: ContextVar[int | None] = ContextVar("brain_actor_id", default=None)
# The acting agent's slug — a human-readable identity forwarded to modules on
# CallTool (the gRPC contract's agent_id). "" when there is no acting agent.
current_actor_slug: ContextVar[str] = ContextVar("brain_actor_slug", default="")


def actor_id() -> int | None:
    """The agent making the current write, or None. Stamp a new row's agent_id
    with this."""
    return current_actor_id.get()


def actor_slug() -> str:
    """The acting agent's slug (or '' if none) — forwarded to modules."""
    return current_actor_slug.get()


async def record_change(
    session: AsyncSession,
    entity: str,
    entity_id: int,
    action: str,
    *,
    old: str | None = None,
    new: str | None = None,
) -> None:
    """Append an audit row for a write. No-op when there is no acting agent.

    `session` is the SAME session as the write, so the log and the change
    commit together or not at all."""
    aid = current_actor_id.get()
    if aid is None:
        return
    session.add(
        ChangeLog(
            entity=entity,
            entity_id=entity_id,
            agent_id=aid,
            action=action,
            old_content=old,
            new_content=new,
        )
    )
