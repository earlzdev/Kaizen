# =============================================================================
# Tracker Models — modules/tracker/models.py
# =============================================================================
# WHAT: SQLAlchemy tables owned by the tracker service (the "Hub" of
#       docs/tracker-architecture.md): the projects that enroll with it, their
#       agent roster, the Directives handed to them, the per-agent Status those
#       Directives produce, and the Questions agents raise for the owner.
#       Deliberately on their OWN DeclarativeBase, separate from every other
#       service's Base.
#
# WHY a separate Base:
#   The tracker is a separate service that happens to share the same Postgres
#   instance (DB-per-service: it owns the `tracker` logical DB). Keeping its
#   metadata separate means it creates exactly its own tables on boot
#   (store.create_tables) and nothing else — no cross-service coupling, and it
#   is trivially split onto another Postgres later (just point it at a new DSN).
#
# WHY "Directive" and not "task" (v2 vocabulary, architecture §1):
#   The word "task" was doing three jobs at once — the unit of work the owner
#   delegates, the project-side task-id a fleet of agents shares, and the
#   handoff files on disk. Naming is load-bearing here, so the unit of work
#   crossing the trust boundary is a **Directive**, and the project-side
#   grouping key it may carry is `task_id` (a string, e.g. "key-rotation").
#
# HOW status flows (the full picture lives in store.ALLOWED_TRANSITIONS):
#   queued ──► dispatched ──► running ──► review ──► done
#                 │              │  ▲                └─► failed
#                 │              ▼  │
#                 │           blocked   (a Question, or out-of-scope work)
#                 └──► queued          (Warden rejected / offline / lease died)
#   `cancelled` is reachable from every non-terminal state.
# =============================================================================

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TrackerBase(DeclarativeBase):
    """Base for tracker-owned tables only — kept apart from other services."""
    pass


# ---------------------------------------------------------------------------
# Vocabulary constants
# ---------------------------------------------------------------------------
# The lifecycle states a Directive may hold (architecture §4). `queued` is where
# every Directive starts; the last three are terminal.
DIRECTIVE_STATUSES = (
    "queued",       # in the Hub, not yet handed to a project
    "dispatched",   # a Warden accepted it; the overseer is starting
    "running",      # pipeline executing
    "blocked",      # waiting on the owner — a Question, or out-of-scope work
    "review",       # PR open, awaiting the owner's decision
    "done",
    "failed",
    "cancelled",
)
TERMINAL_STATUSES = ("done", "failed", "cancelled")

# The states in which a project is actively responsible for a Directive and is
# expected to hold a live lease (Heartbeat). ONLY these are swept on lease
# expiry: `review` deliberately is not — there the owner, not the project, owes
# the next move, so no heartbeat is expected and requeueing would be wrong.
LEASED_STATUSES = ("dispatched", "running", "blocked")

# The Directive kinds the Hub understands. A project narrows this to the subset
# it supports via its manifest (`ProjectManifest.kinds`); the Hub validates a
# new Directive against BOTH — this tuple catches typos, the manifest catches
# "this project can't do that".
#
# WHY `ask` is its own kind and not a flag on `brainstorm`: the kind IS the
# contract with the project side. A project opts into answering questions the
# same way it opts into anything else — by declaring it in the manifest — and the
# Warden routes on the kind alone. A boolean on `brainstorm` would mean every
# reader (the Hub, the panel, Кая, the Warden) had to check two fields to know
# whether a run writes files, and the one that forgot would spawn a fleet for a
# question the owner wanted answered in a sentence.
DIRECTIVE_KINDS = (
    "develop", "fix", "refactor", "research", "review", "epic", "brainstorm",
    "analyze", "ask",
    # A live owner<->Warden dialogue (the "позови альфреда" tunnel). Unlike
    # every other kind, it never reaches a terminal state on its own — it
    # stays `running` until the owner explicitly ends it (Cancel), because
    # there is no "the work is done" signal a conversation can emit itself.
    "converse",
    # Ship the project's own current main branch to prod: opens (or merges,
    # per Directive.intent) the PR from main into deploy. Opt-in per project
    # like every other kind — only advertised when its manifest includes it
    # (infra/agentkit renders /deploy only when spec["deploy"] is set).
    "deploy",
)

# Lifecycle of a project row:
#   pending   it asked to enroll and is waiting for the owner's approval
#   approved  the owner said yes — but no token exists yet
#   active    the Warden claimed its token; the project is live
#   disabled  kept for history, dispatches nothing
#
# WHY `approved` and `active` are separate states (and the token is minted at
# CLAIM time, not at approval): a token sitting on an approved-but-unclaimed row
# is a credential at rest with nothing guarding it. Brain's agent enrollment
# learned this the hard way (see brain/enroll.py) and now mints inside the first
# secret-authenticated claim. Same trust problem, same answer.
PROJECT_STATES = ("pending", "approved", "active", "disabled")

# Per-agent Status values a project mirrors upward (architecture §3.2).
# `failed`/`cancelled` are the terminal rows DirectiveJob.finish() writes: the
# invariant "the agent's status is DERIVED from the JobResult" only holds if
# the Hub accepts the failing results too. Rejecting them here (as this tuple
# once did) meant the status FILE said `failed` while the Hub's row froze at
# `in_progress` forever — a record contradicting itself, silently, in exactly
# the case finish() was built for.
AGENT_STATUS_STATES = (
    "idle", "pending", "in_progress", "blocked", "review", "done",
    "failed", "cancelled",
)


class TrackerProject(TrackerBase):
    """One external project that connects to the tracker.

    Two ways a row is born, and the difference is `state`:
      - **Enrollment** (the v2 path, architecture §5): a Warden calls
        `Register` with no token → row created `pending`, token NULL. The owner
        approves → a token is minted and the row goes `active`.
      - **Admin register** (the v1 poller path, still supported): the owner
        creates the project directly and is handed the token to paste into a
        ~30-line poller. Those rows start `active`.

    `manifest` is the last ProjectManifest the project sent, stored whole as
    JSONB. WHY keep the raw manifest rather than only the parsed columns: the
    roster and the supported-kinds list grow as a project grows, and Кая reads
    capabilities straight from it — a stale, partially-parsed copy is worse than
    none. The columns beside it (`purpose`, `max_concurrent`, ...) are just the
    fields the Hub itself queries on.
    """

    __tablename__ = "tracker_projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # One line: what this project is for. Shown to Кая so she can pick a target
    # without the owner spelling out the project name every time.
    purpose: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # Per-project bearer token, minted when the Warden claims it (or straight
    # away on admin register). NULLable because a project that has not claimed
    # yet has none — indexed because every project-authenticated call looks the
    # project up by this value.
    token: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    # sha256 of the secret the enrolling Warden generated. NEVER the secret
    # itself: this row only has to RECOGNISE the claimant, not be able to
    # impersonate it. Without this, whoever calls Register first after the owner
    # clicks "approve" walks away with the project's token.
    enroll_secret_hash: Mapped[str | None] = mapped_column(String(64))
    # One of PROJECT_STATES.
    state: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # The whole ProjectManifest as sent by the Warden (kinds, roster, repo, ...).
    manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    # How many Directives this project will run at once. Mirrored from the
    # manifest so the dispatcher can filter in SQL instead of dialing to ask.
    max_concurrent: Mapped[int] = mapped_column(Integer, default=1)
    # host:port of this project's Warden, for the Hub→Warden dispatch leg.
    # NULL for the poller tier — those projects are never dialed.
    grpc_addr: Mapped[str | None] = mapped_column(String(255))
    # Last time the project spoke to us (Register/Heartbeat/PushStatus/claim).
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def to_dict(self) -> dict:
        """Serialize for the API/panel. NOTE: never includes `token`."""
        return {
            "id": self.id,
            "name": self.name,
            "purpose": self.purpose,
            "description": self.description,
            "state": self.state,
            "manifest": self.manifest or {},
            "max_concurrent": self.max_concurrent,
            # Whether a Warden is wired up at all — the panel shows the tier
            # ("warden" vs "poller") and the address is internal detail.
            "has_warden": bool(self.grpc_addr),
            "grpc_addr": self.grpc_addr,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TrackerAgent(TrackerBase):
    """A member of a project's team — an AI agent or a human developer.

    WHY a first-class table (agents used to be *derived* from a Directive's
    claimed_by): a project owns its roster explicitly, so idle members and
    their roles show up in the team view even before they've touched anything.
    In v2 this table is refreshed from each `Register` manifest, so the roster
    the panel shows is the roster the project actually runs.
    """

    __tablename__ = "tracker_agents"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_agent_project_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_projects.id", ondelete="CASCADE"), index=True
    )
    # Matches the `agent_slug` a project sends on Status, so activity joins.
    # This is an IDENTIFIER, not a label — it must stay stable and unique.
    name: Mapped[str] = mapped_column(String(255))
    # What a human should be called on the chart ("Charles Xavier"). Kept apart
    # from `name` because that one is a join key: renaming the person must
    # never break the link to their reported Status.
    display_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(255))
    # "ai" for an AI agent, "human" for a developer on the team.
    kind: Mapped[str] = mapped_column(String(16), default="ai")
    # Which model the persona runs on, when the manifest says. Purely
    # informational — the Hub never picks models.
    model: Mapped[str | None] = mapped_column(String(255))
    # The three fields that describe the fleet's SHAPE rather than one member:
    #   tier       where it sits — one of roster.AGENT_TIERS (owner → product
    #              → architect → lead → developer → reviewer). The standard
    #              vocabulary; the free-text `role` above is the label a human
    #              reads. `owner` is the human; `product` is the PO agent that
    #              owns WHAT is built and sits above the architect.
    #   area       which part of the project it works on (backend, frontend, …)
    #   reports_to another agent's `name` on this same project
    #
    # All three are DISPLAY information — the Hub dispatches to a project, never
    # to a persona, so nothing here affects routing.
    #
    # WHY they are stored and not derived at render time: every project's fleet
    # is different, so a guess that fits one misfits the next. modules/tracker/
    # roster.py normalises whatever a project sent into this vocabulary ONCE, on
    # write, so the database holds the answer, every reader agrees, and a wrong
    # guess is visible in `GET /agents` where it can be corrected.
    tier: Mapped[str] = mapped_column(String(32), default="developer")
    area: Mapped[str | None] = mapped_column(String(64))
    reports_to: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "display_name": self.display_name,
            "role": self.role,
            "kind": self.kind,
            "model": self.model,
            "tier": self.tier,
            "area": self.area,
            "reports_to": self.reports_to,
        }


AGENT_KINDS = ("ai", "human")


class TrackerDirective(TrackerBase):
    """One unit of work the owner delegates to a project (architecture §1).

    Was `TrackerTask` / `tracker_tasks` in v1. The rename is not cosmetic: a
    Directive crosses a trust boundary and is tracked here, while the project's
    own task-id (`task_id` below) groups the files and agents working on it
    INSIDE the project. Two scopes, two names, no collisions.
    """

    __tablename__ = "tracker_directives"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    # The owner's ACTUAL words. The project's overseer persona interprets these
    # — the Hub deliberately does not pre-structure them (architecture §6).
    description: Mapped[str | None] = mapped_column(Text)
    # One of DIRECTIVE_KINDS — which pipeline the project should route to.
    kind: Mapped[str] = mapped_column(String(32), default="develop", index=True)
    # One of DIRECTIVE_STATUSES. Indexed: the dispatcher and the atomic claim
    # both filter on it.
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    # Lower runs first. The dispatcher orders by (priority, id), so equal
    # priorities keep FIFO order and `reprioritise` only has to rewrite this.
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    # The PROJECT-side task id (e.g. "key-rotation"): names the
    # docs/tracker/{task_id}/ tree and groups its Handoffs. Assigned by the
    # project on accept, or supplied by the owner to continue existing work.
    task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    # Set when this Directive was spawned by decomposing an epic (case 13).
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracker_directives.id", ondelete="SET NULL"), index=True
    )
    # Per-Directive merge permission — NEVER a global setting. This single bool
    # is what stands between an autonomous fleet and an unreviewed merge.
    auto_merge: Mapped[bool] = mapped_column(Boolean, default=False)
    # Free-form agent identifier holding this Directive: the poller tier's
    # claimant, or the project's overseer for the Warden tier.
    claimed_by: Mapped[str | None] = mapped_column(String(255))
    # Human-readable result the project reports back; shown to the owner.
    summary: Mapped[str | None] = mapped_column(Text)
    # List of {type, url} produced by the fleet (PRs, files, dashboards...).
    artifacts: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    # While a project holds this Directive it must Heartbeat before this
    # deadline; the sweeper requeues anything past it. NULL means "nobody is
    # on the hook" — which is exactly the poller tier's situation (pollers
    # never heartbeat, so they must never be swept).
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    # How many times the Hub has tried to hand this to a Warden. Drives the
    # dispatch backoff and the "tell the owner only after N attempts" rule, so
    # a container restart never generates a Telegram message.
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self) -> dict:
        """Serialize for the API (JSON-safe)."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "status": self.status,
            "priority": self.priority,
            "task_id": self.task_id,
            "parent_id": self.parent_id,
            "auto_merge": self.auto_merge,
            "claimed_by": self.claimed_by,
            "summary": self.summary,
            "artifacts": self.artifacts or [],
            "error": self.error,
            "lease_expires_at": (
                self.lease_expires_at.isoformat() if self.lease_expires_at else None
            ),
            "dispatch_attempts": self.dispatch_attempts,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TrackerAgentStatus(TrackerBase):
    """The LATEST Status one of a project's agents reported for one Directive.

    WHY overwritten, not appended (architecture §1): Status is observability,
    not history — "where is Anderson right now", asked across every project at
    once (`project_activity`). The audit trail that matters lives in the
    project's own Handoff files, which survive container restarts and can be
    read by a human. Storing every transition here would buy a second, worse
    copy of that trail.

    Unique on (directive_id, agent_slug) so an upsert is the natural write.
    """

    __tablename__ = "tracker_agent_status"
    __table_args__ = (
        UniqueConstraint("directive_id", "agent_slug", name="uq_status_directive_agent"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    directive_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_directives.id", ondelete="CASCADE"), index=True
    )
    # e.g. "architect-xavier", "backend-dev-anderson" — the project's persona slug.
    agent_slug: Mapped[str] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(255))
    # One of AGENT_STATUS_STATES.
    state: Mapped[str] = mapped_column(String(32), default="idle")
    progress: Mapped[str | None] = mapped_column(Text)
    blockers: Mapped[str | None] = mapped_column(Text)
    # Which pipeline phase the work is in ("TZ", "implementation", "review"...).
    # Free-form on purpose: the Hub does not model pipeline internals (case 10).
    phase: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "directive_id": self.directive_id,
            "agent_slug": self.agent_slug,
            "role": self.role,
            "state": self.state,
            "progress": self.progress,
            "blockers": self.blockers,
            "phase": self.phase,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TrackerAgentUsage(TrackerBase):
    """One persona turn's token/cost accounting (architecture §8).

    WHY appended, not overwritten like `TrackerAgentStatus`: Status duplicates
    a trail that already lives in the project's own Handoff files, so keeping
    only the latest row avoids a second, worse copy of that history. Usage has
    no such duplicate anywhere else — it is the only record of what a turn
    cost — so it is kept as a ledger. One row per `run_persona_turn()` call
    that reported nonzero usage; a turn that reports none never inserts a row.

    Powers the panel's Analytics tab: summed by `agent_slug`/`phase` per
    project or Directive, this is what answers "did the crew cost more than
    Alfred alone would have" from data instead of a guess.
    """

    __tablename__ = "tracker_agent_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    directive_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_directives.id", ondelete="CASCADE"), index=True
    )
    agent_slug: Mapped[str] = mapped_column(String(255))
    phase: Mapped[str | None] = mapped_column(String(255))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "directive_id": self.directive_id,
            "agent_slug": self.agent_slug,
            "phase": self.phase,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": self.cost_usd,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TrackerQuestion(TrackerBase):
    """A question an agent needs the owner to answer before it can continue.

    The row is the durable half of a blocking RPC: the asking agent's
    `AskOwner` call is held open by the Hub while this row waits for `answer`.
    Durable so a Hub restart doesn't lose the question, and so the panel and
    Кая can both show what is waiting.
    """

    __tablename__ = "tracker_questions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    directive_id: Mapped[int] = mapped_column(
        ForeignKey("tracker_directives.id", ondelete="CASCADE"), index=True
    )
    agent_slug: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    # Optional choices the asking agent offered, so Кая can present buttons.
    suggested: Mapped[list] = mapped_column(JSONB, default=list)
    # NULL until the owner answers. `answered_at` is what the held-open RPC
    # polls for — an empty-string answer is still an answer, so presence must
    # be tested on `answered_at`, never on truthiness of `answer`.
    answer: Mapped[str | None] = mapped_column(Text)
    asked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    answered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    # When the asking agent stops waiting. Recorded on the ROW, not just held in
    # the waiting coroutine, so the fact survives a Hub restart — and so the
    # owner's "questions waiting for me" list can drop the ones nobody is
    # listening to any more. A question the owner answers after this moment
    # goes nowhere, and showing it would be a small lie.
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "directive_id": self.directive_id,
            "agent_slug": self.agent_slug,
            "text": self.text,
            "suggested": self.suggested or [],
            "answer": self.answer,
            "answered": self.answered_at is not None,
            "asked_at": self.asked_at.isoformat() if self.asked_at else None,
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
