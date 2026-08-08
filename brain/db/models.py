# =============================================================================
# Brain data schema (Phase 1) — brain/db/models.py
# =============================================================================
# WHAT: The frozen schema of Brain's own database — the "shared memory + agent
#       registry + access-list + provenance" from docs/plans/kaizen-v2-rollout.md
#       Phase 1. Five tables: agents, access_rules, facts, profile, reminders,
#       plus change_log for provenance.
#
# WHY these tables reuse the v1 shapes but drop `user_id`:
#   The provenance design (agents + change_log + agent_id stamping) is lifted
#   from the monolith's Context Service work (app/core/provenance.py, migration
#   008) — the plan says to reuse it. But Brain is SINGLE-TENANT: it holds the
#   memory about ONE owner. So facts/profile/reminders carry no user_id (there
#   is only one subject); they carry only `agent_id` — WHICH agent wrote it, for
#   the audit trail. That is the actor/subject split: subject is implicit (the
#   owner), actor is recorded.
#
# WHY the access-list is allow-by-default with deny rows:
#   Phase 2 enforces "allow-by-default, deny surgically". An AccessRule is an
#   EXCEPTION carved out of the default: no row for (agent, tool) ⇒ allowed. A
#   row with allowed=false denies. Scope widens from tool -> module -> nothing:
#   module=null,tool=null is a blanket rule for the agent; module set + tool
#   null denies/allows a whole module; both set targets one tool.
#
# WHY embedding is Vector(384):
#   Reuses the existing local embedder (sentence-transformers, 384 dims) so
#   Brain's fact recall is the same pgvector cosine search the monolith already
#   proved (see app/db/models.py Chunk/Memory).
#
# HOW: models here are the source of truth for Brain's Alembic autogenerate
#   target (brain/migrations/env.py imports this Base). Migration 001 creates
#   exactly these tables. `from brain.db.session import get_session` to query.
# =============================================================================

import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Dimensionality of the local embedding model (paraphrase-multilingual-MiniLM-
# L12-v2, see brain/embedder.py). Must match the embedder Brain uses, or cosine
# search returns garbage.
EMBED_DIM = 384

# Who a fired reminder is for (Reminder.audience). Named constants because
# three modules key on these strings: the store, the sweeper (which delivery
# event kind to send) and the tools that create reminders.
AUDIENCE_OWNER = "owner"
AUDIENCE_AGENT = "agent"


class Base(DeclarativeBase):
    """Declarative base for Brain's schema. The models ARE the schema: boot
    runs metadata.create_all (no Alembic — owner's pre-prod decision), so
    importing this module registers every table below."""


class Agent(Base):
    """One identity per agent that reaches Brain (Кая, Кузя, ...).

    token_hash stores only sha256(token) — never the plaintext, same reasoning
    as the v1 AgentStore. delivery_addr is where Brain PUSHES events to this
    agent (Phase 6 callbacks); nullable because an agent may be call-only."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    # Where Brain reaches the agent to push events (Phase 6). Null = no callbacks.
    delivery_addr: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_brain_agents_slug", "slug"),
        Index("ix_brain_agents_token_hash", "token_hash"),
    )


class AccessRule(Base):
    """One allow/deny exception in the access-list for an agent.

    Allow-by-default: the ABSENCE of a matching row means the agent may call the
    tool. A row carves an exception. Scope by how many of (module, tool) are set:
      module=NULL, tool=NULL -> blanket rule for the agent
      module set, tool=NULL  -> whole-module rule
      module set, tool set   -> single-tool rule
    `allowed=false` is the common case (a surgical deny); `allowed=true` lets an
    allow override a broader deny in later, more nuanced enforcement."""

    __tablename__ = "access_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE")
    )
    # Null = applies to all modules / all tools at that level.
    module: Mapped[str | None] = mapped_column(String(64))
    tool: Mapped[str | None] = mapped_column(String(64))
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_brain_access_rules_agent", "agent_id"),)


class Fact(Base):
    """One durable fact in the shared memory about the owner, with its vector
    embedding for similarity recall.

    No user_id: Brain is single-tenant, the subject is always the one owner.
    agent_id records WHICH agent wrote the fact (provenance), null for a
    system/seed write."""

    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Profile(Base):
    """Stable, deterministically-needed facts about the owner (timezone, home
    location) kept as first-class fields, not fuzzy facts.

    Single-tenant ⇒ this table holds exactly one row (the owner). Same rationale
    as the v1 UserProfile: the timezone decides when reminders fire, so it must
    be exact and always present, never similarity-recalled."""

    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timezone: Mapped[str | None] = mapped_column(String(64))        # IANA
    home_location: Mapped[str | None] = mapped_column(String(255))  # free text
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Reminder(Base):
    """Something to happen at a specific time. Durable state (a sleeping
    coroutine dies with the process); a sweeper fires due rows. agent_id records
    which agent set it.

    `audience` says WHO the firing is for:
      "owner" — the classic reminder: its text is delivered to the owner.
      "agent" — a note the agent left ITSELF: firing WAKES the agent, which
                then decides what (if anything) to say. This is what lets an
                agent plan a future action ("ask how the flight went") instead
                of only relaying text."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text)
    # The concrete instant the reminder fires, stored as timestamptz (an absolute
    # moment in UTC). ALWAYS timezone-aware — a naive value would be assumed UTC
    # by the driver and fire at the wrong wall-clock time.
    due_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    # The IANA zone the reminder was set in (e.g. "Europe/Moscow"). Kept so the
    # owner sees it in their own time, and so a recurring reminder advances in
    # LOCAL wall-clock time (DST-safe), not by a fixed UTC delta.
    tz: Mapped[str | None] = mapped_column(String(64))
    recurrence: Mapped[str] = mapped_column(String(20), default="none")  # none|daily|weekly
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    # owner|agent — see the class docstring. server_default (not just default)
    # so rows that predate this column read as "owner" without a data migration.
    audience: Mapped[str] = mapped_column(
        String(16), default=AUDIENCE_OWNER, server_default=AUDIENCE_OWNER
    )
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Episode(Base):
    """One owner↔agent exchange in the shared conversation archive.

    WHY in Brain and not in each agent's DB: agents' local history is only a
    prompt window; nothing can search it (and a Кая-local tool couldn't be
    served to the CLI backend, which gets tools from Brain over MCP). Here every
    agent logs its exchanges, and one search tool serves them all — including
    ones that connect later (Кузя).

    agent_slug is denormalized (agent_id can go NULL if an agent row is ever
    deleted) — the "search only MY dialogs" filter must keep working."""

    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    agent_slug: Mapped[str] = mapped_column(String(64))
    owner_text: Mapped[str] = mapped_column(Text)
    agent_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_brain_episodes_slug_created", "agent_slug", "created_at"),)


class ChangeLog(Base):
    """The audit trail: one row per agent-attributable write to the shared
    memory. Lifted from the v1 provenance design — records who changed what,
    when, and the before/after content."""

    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity: Mapped[str] = mapped_column(String(20))      # fact|profile|reminder
    entity_id: Mapped[int] = mapped_column()
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(10))      # create|update|delete
    old_content: Mapped[str | None] = mapped_column(Text)
    new_content: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Enrollment(Base):
    """A pending/decided request from an agent to connect (device pairing).

    WHY: instead of minting a token by hand and pasting it into each agent, an
    agent asks to connect (POST /enroll), the owner APPROVES it in the terminal,
    and the agent then fetches its token (POST /enroll/status) and stores it
    itself. One row per agent slug. Statuses: pending -> approved -> claimed
    (terminal until a new request re-arms the row), or rejected.

    WHY no token at rest (Step 4 of ARCHITECTURE_REVIEW.md): the token is minted
    INSIDE the first secret-authenticated claim and returned directly — it is
    never stored. `secret_hash` authenticates the claiming agent (only whoever
    created the request can claim)."""

    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|claimed|rejected
    # LEGACY (pre-Step-4): the plaintext token used to wait here between approve
    # and claim. Always NULL now (kept because create_all can't drop columns —
    # no migrations by owner's decision); claim() mints the token on the spot.
    token: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
