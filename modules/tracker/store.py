# =============================================================================
# Tracker Store — modules/tracker/store.py
# =============================================================================
# WHAT: All database operations for the tracker (the Hub), as async functions
#       over the tracker's own SQLAlchemy engine (modules/tracker/session.py).
#
# WHY its own session module:
#   The tracker owns the `tracker` logical DB (DB-per-service), so it builds its
#   own engine/session factory from its own settings and creates only ITS OWN
#   tables (TrackerBase.metadata) on boot.
#
# THE TWO SUBTLE PARTS
#
#   1. Atomic claim (`claim_next`, the poller tier).
#      Two of a project's pollers may ask for work at the exact same moment. A
#      naive "SELECT one queued, then UPDATE it" races: both read the same row,
#      both claim it. Postgres' `FOR UPDATE SKIP LOCKED` fixes this — the first
#      transaction locks the row and the second skips past it to the next
#      queued Directive (or gets nothing).
#
#   2. The transition map (`ALLOWED_TRANSITIONS`).
#      v1 let any report set any status: a `done` Directive could be moved back
#      to `running`, and something never claimed could be `done`d. Every status
#      change now goes through `_apply_transition`, in ONE place, so the
#      lifecycle in docs/tracker-architecture.md §4 is actually enforced rather
#      than merely documented.
# =============================================================================

import datetime
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass

from sqlalchemy import func, or_, select, text

from modules.tracker import roster as roster_vocab
from modules.tracker.session import async_session, default_engine
from modules.tracker.models import (
    DIRECTIVE_KINDS,
    LEASED_STATUSES,
    TERMINAL_STATUSES,
    TrackerAgent,
    TrackerAgentStatus,
    TrackerAgentUsage,
    TrackerBase,
    TrackerDirective,
    TrackerProject,
    TrackerQuestion,
)

logger = logging.getLogger(__name__)


class TransitionError(ValueError):
    """An illegal Directive status change was attempted.

    Distinct from "not found" (None) so callers can answer 409 vs 404 — and so
    a buggy project gets a precise message instead of a silent no-op.
    """


# The state machine of docs/tracker-architecture.md §4, as data.
#
# Reading notes for the non-obvious edges:
#   dispatched → queued   the Warden rejected it, went offline, or its lease
#                         expired — the Directive goes back in line, unharmed.
#   running/blocked → queued  same, via the sweeper (case 8).
#   review → running      the owner sent it back for more work, or granted
#                         auto-merge and the project re-opened the pipeline.
#   *      → cancelled    the owner may abort anything not already terminal.
#   terminal states have NO outgoing edges at all: once the owner has been told
#                         "done", that Directive's story cannot be rewritten.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued":     {"dispatched", "failed", "cancelled"},
    "dispatched": {"running", "blocked", "review", "done", "failed", "queued", "cancelled"},
    "running":    {"blocked", "review", "done", "failed", "queued", "cancelled"},
    "blocked":    {"running", "review", "done", "failed", "queued", "cancelled"},
    "review":     {"running", "done", "failed", "cancelled"},
    "done":       set(),
    "failed":     set(),
    "cancelled":  set(),
}


def _now() -> datetime.datetime:
    """Timezone-aware UTC now — every DateTime column here is timezone=True."""
    return datetime.datetime.now(datetime.timezone.utc)


def _apply_transition(directive: TrackerDirective, target: str) -> bool:
    """Move `directive` to `target`, enforcing ALLOWED_TRANSITIONS.

    Returns True if the status actually changed, False if it was already
    `target`. Raises TransitionError on an illegal jump.

    WHY a same-state report is a no-op and NOT an error: a project retries
    PushStatus/PushReport with backoff when the Hub was restarting (case 9), so
    the Hub will legitimately see `done` twice. Rejecting the second one would
    make an honest project retry forever.
    """
    if directive.status == target:
        return False
    allowed = ALLOWED_TRANSITIONS.get(directive.status, set())
    if target not in allowed:
        raise TransitionError(
            f"Directive #{directive.id} cannot go {directive.status} -> {target} "
            f"(allowed from {directive.status}: {sorted(allowed) or 'nothing, it is terminal'})"
        )
    directive.status = target
    # Leases exist to answer "is somebody still on the hook for this?". In
    # `review` and the terminal states nobody is, so the lease is dropped —
    # otherwise the sweeper would eventually requeue a Directive that is simply
    # waiting for the owner.
    if target not in LEASED_STATUSES:
        directive.lease_expires_at = None
    return True


async def create_tables() -> None:
    """Bring the tracker's database to the latest migration.

    This is the service the old create_all approach actually broke: `purpose`
    was added to the model long after the production database was created, so
    every project listing 500'd on a column that no boot would ever add.
    """
    from infra.migrations.runner import upgrade

    await upgrade("tracker")


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
async def create_project(
    name: str,
    description: str | None = None,
    *,
    purpose: str | None = None,
    state: str = "active",
    grpc_addr: str | None = None,
    manifest: dict | None = None,
    max_concurrent: int = 1,
) -> TrackerProject:
    """Register a project. `active` projects get a token minted here and now.

    Raises ValueError if the name is already taken — names are the human handle
    Кая delegates against, so they must be unique.

    A `pending` project (the enrollment path) is created WITHOUT a token: there
    is nothing to steal while the owner has not yet said yes.
    """
    async with async_session() as session:
        existing = await session.scalar(
            select(TrackerProject).where(TrackerProject.name == name)
        )
        if existing is not None:
            raise ValueError(f"A project named '{name}' already exists")

        project = TrackerProject(
            name=name,
            purpose=purpose,
            description=description,
            token=secrets.token_urlsafe(32) if state == "active" else None,
            state=state,
            grpc_addr=grpc_addr,
            manifest=manifest or {},
            max_concurrent=max_concurrent,
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)
        return project


async def list_projects(state: str | None = None) -> list[TrackerProject]:
    async with async_session() as session:
        stmt = select(TrackerProject)
        if state is not None:
            stmt = stmt.where(TrackerProject.state == state)
        result = await session.execute(stmt.order_by(TrackerProject.id))
        return list(result.scalars().all())


async def get_project(project_id: int) -> TrackerProject | None:
    async with async_session() as session:
        return await session.get(TrackerProject, project_id)


async def get_project_by_token(token: str | None) -> TrackerProject | None:
    """Look up the project a bearer token belongs to (project-auth path).

    The empty/None guard is load-bearing: `pending` projects have a NULL token,
    and a caller presenting no token at all must never match one of them.
    """
    if not token:
        return None
    async with async_session() as session:
        return await session.scalar(
            select(TrackerProject).where(TrackerProject.token == token)
        )


async def get_project_by_name(name: str) -> TrackerProject | None:
    async with async_session() as session:
        return await session.scalar(
            select(TrackerProject).where(TrackerProject.name == name)
        )


# ---------------------------------------------------------------------------
# Enrollment (architecture §5) — the Warden-facing half of project registration
# ---------------------------------------------------------------------------
def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _secret_matches(project: TrackerProject, secret: str) -> bool:
    """Constant-time comparison against the stored hash (a plain != leaks the
    match length via timing) — the same guard brain/enroll.py uses."""
    if not project.enroll_secret_hash or not secret:
        return False
    return hmac.compare_digest(project.enroll_secret_hash, _hash_secret(secret))


@dataclass
class RegisterOutcome:
    """What `register_project` decided. Everything the Hub servicer needs to
    build a RegisterAck without re-querying — and `created` so it knows when to
    wake the owner."""

    project: TrackerProject | None = None
    pending: bool = True
    token: str = ""            # non-empty ONLY on the call that mints it
    message: str = ""
    created: bool = False      # first time we have ever seen this project
    unauthenticated: bool = False


async def register_project(
    name: str,
    manifest: dict,
    *,
    token: str | None = None,
    secret: str = "",
    grpc_addr: str = "",
    purpose: str = "",
    description: str = "",
    max_concurrent: int = 1,
) -> RegisterOutcome:
    """The whole Register decision tree, in one transaction.

    The security shape is deliberately identical to brain/enroll.py, because it
    is the same trust problem one hop further out:

      token, valid          -> refresh the manifest. This is the steady state.
      token, unknown        -> UNAUTHENTICATED. The kit clears it and re-enrolls.
      no token, new name    -> create `pending`, remember sha256(secret), tell
                               the owner. Nothing is issued.
      no token, pending     -> refresh, re-arm the secret. A pending row grants
                               nothing, so the latest asker may hold the slot.
      no token, approved    -> the secret decides. MATCHES: mint the token now,
                               go `active`. DIFFERS: the approval is revoked and
                               the row drops back to `pending` — an approval the
                               owner gave to one claimant must never be
                               inherited by another.
      no token, active      -> refuse quietly. Handing the token to whoever asks
                               would be a takeover; the recovery path is the
                               owner rotating it (case 16), which puts the row
                               back to `pending` where a fresh claim is safe.
      no token, disabled    -> refuse, and say so.
    """
    async with async_session() as session:
        if token:
            project = await session.scalar(
                select(TrackerProject).where(TrackerProject.token == token)
            )
            if project is None:
                return RegisterOutcome(unauthenticated=True, message="unknown project token")
            if project.state == "disabled":
                return RegisterOutcome(
                    project=project, pending=True,
                    message=f"project '{project.name}' is disabled",
                )
            _apply_manifest(project, manifest, grpc_addr, purpose, description, max_concurrent)
            project.last_seen_at = _now()
            await session.commit()
            await session.refresh(project)
            return RegisterOutcome(project=project, pending=False, message="manifest refreshed")

        # FOR UPDATE is what makes the decision tree below atomic. Everything
        # after this point is read-then-write on ONE row — "is it approved, does
        # the secret match, mint a token" — and two Wardens racing the same
        # approval would otherwise both read `approved` and both act on it: the
        # honest claimant would be handed a token that the impostor's revocation
        # immediately buries, or two tokens would be minted and only the last
        # commit would be the live one, leaving the other Warden holding a dead
        # credential it believes in. The lock serialises them, so the second one
        # decides against the state the first one left behind.
        project = await session.scalar(
            select(TrackerProject).where(TrackerProject.name == name).with_for_update()
        )
        if project is None:
            project = TrackerProject(
                name=name,
                state="pending",
                enroll_secret_hash=_hash_secret(secret) if secret else None,
                manifest={},
            )
            session.add(project)
            _apply_manifest(project, manifest, grpc_addr, purpose, description, max_concurrent)
            project.last_seen_at = _now()
            await session.commit()
            await session.refresh(project)
            return RegisterOutcome(
                project=project, pending=True, created=True,
                message=f"'{name}' is waiting for the owner's approval",
            )

        if project.state == "active":
            return RegisterOutcome(
                project=project, pending=True,
                message=(
                    f"'{name}' is already enrolled — ask the owner to rotate its "
                    "token if this Warden lost it"
                ),
            )
        if project.state == "disabled":
            return RegisterOutcome(
                project=project, pending=True, message=f"project '{name}' is disabled"
            )

        if project.state == "approved" and _secret_matches(project, secret):
            project.token = secrets.token_urlsafe(32)
            project.state = "active"
            _apply_manifest(project, manifest, grpc_addr, purpose, description, max_concurrent)
            project.last_seen_at = _now()
            await session.commit()
            await session.refresh(project)
            logger.info("Project '%s' claimed its token (enrollment complete)", name)
            return RegisterOutcome(
                project=project, pending=False, token=project.token,
                message="approved — token issued",
            )

        if project.state == "approved":
            logger.warning(
                "Project '%s': approved, but re-registered with a DIFFERENT secret "
                "— revoking the approval, back to pending.", name,
            )
        project.state = "pending"
        project.enroll_secret_hash = _hash_secret(secret) if secret else None
        _apply_manifest(project, manifest, grpc_addr, purpose, description, max_concurrent)
        project.last_seen_at = _now()
        await session.commit()
        await session.refresh(project)
        return RegisterOutcome(
            project=project, pending=True,
            message=f"'{name}' is waiting for the owner's approval",
        )


def _apply_manifest(
    project: TrackerProject,
    manifest: dict,
    grpc_addr: str,
    purpose: str,
    description: str,
    max_concurrent: int,
) -> None:
    """Copy a freshly-received manifest onto the project row.

    The whole manifest is kept as JSONB and the few fields the Hub itself
    queries on are mirrored into columns. Blank values do NOT overwrite what we
    already know: a Warden that omits `purpose` on a refresh is saying nothing
    about it, not saying it is empty.
    """
    project.manifest = manifest
    if grpc_addr:
        project.grpc_addr = grpc_addr
    if purpose:
        project.purpose = purpose
    if description:
        project.description = description
    if max_concurrent > 0:
        project.max_concurrent = max_concurrent


async def approve_project(name: str) -> TrackerProject | None:
    """The owner's yes. Flips `pending` -> `approved` and mints NOTHING —
    the token comes into existence inside the claimant's next Register."""
    async with async_session() as session:
        project = await session.scalar(
            select(TrackerProject).where(TrackerProject.name == name)
        )
        if project is None or project.state not in ("pending", "approved"):
            return None
        project.state = "approved"
        await session.commit()
        await session.refresh(project)
        logger.info("Project '%s' approved by the owner", name)
        return project


async def rotate_project_token(
    name: str, *, reset_secret: bool = False
) -> TrackerProject | None:
    """Invalidate a project's token (architecture §7 case 16).

    Drops the row to `pending`, so the Warden's next call gets UNAUTHENTICATED,
    re-enrolls, and waits for the owner's approval. Deliberately NOT a silent
    re-issue: a rotation is something you do because you no longer trust what
    was out there.

    WHY the enrollment secret SURVIVES by default: it is the Warden's identity,
    not its credential — the Hub only ever holds its sha256. Clearing it would
    cost the owner a second approval every time: the row would go `approved`
    with no hash, the honest Warden's secret would fail to match, that mismatch
    would revoke the approval, and the owner would have to say yes again.

    `reset_secret=True` is for the case where the identity itself is suspect —
    the whole container was compromised, so whoever took the token took the
    secret beside it. Then the next claimant must prove itself anew.
    """
    async with async_session() as session:
        project = await session.scalar(
            select(TrackerProject).where(TrackerProject.name == name).with_for_update()
        )
        if project is None:
            return None
        project.token = None
        if reset_secret:
            project.enroll_secret_hash = None
        project.state = "pending"
        await session.commit()
        await session.refresh(project)
        logger.info(
            "Project '%s' token rotated (secret %s) — awaiting re-enrollment",
            name, "reset" if reset_secret else "kept",
        )
        return project


async def set_project_state(name: str, state: str) -> TrackerProject | None:
    """Enable/disable a project without losing its history or its token."""
    async with async_session() as session:
        project = await session.scalar(
            select(TrackerProject).where(TrackerProject.name == name)
        )
        if project is None:
            return None
        project.state = state
        await session.commit()
        await session.refresh(project)
        return project


async def ensure_owner(project_id: int, owner_name: str) -> None:
    """Make sure the human this project works FOR is on its chart.

    WHY the Hub seeds this and no project declares it: the owner is the same
    person for every project, they are not an agent, and a fleet chart that
    starts at the architect hides who the architect is actually working for.
    It is created once and then left alone — if the owner renames it or gives
    it a role, that edit survives every subsequent manifest refresh.
    """
    if not owner_name:
        return
    async with async_session() as session:
        existing = await session.scalar(
            select(TrackerAgent).where(
                TrackerAgent.project_id == project_id,
                TrackerAgent.tier == roster_vocab.OWNER,
            )
        )
        if existing is not None:
            return
        session.add(
            TrackerAgent(
                project_id=project_id, name=owner_name, display_name=owner_name,
                # "Owner", not "Product Owner": a project may run a PO AGENT
                # (tier `product`) directly below, and two rows both labelled
                # "Product Owner" is exactly the confusion the chart exists to
                # remove. This is the human the work is for.
                role="Owner", kind="human", tier=roster_vocab.OWNER,
            )
        )
        await session.commit()


async def sync_roster(project_id: int, roster: list[dict]) -> int:
    """Make the stored roster match the manifest's. Returns how many are known.

    Upserts by name and leaves unlisted members alone: a manifest is what the
    project runs TODAY, and a persona retired last week is still the author of
    Handoffs in the history the panel renders.
    """
    if not roster:
        return 0
    async with async_session() as session:
        existing = {
            a.name: a
            for a in (
                await session.execute(
                    select(TrackerAgent).where(TrackerAgent.project_id == project_id)
                )
            ).scalars()
        }
        for raw in roster:
            # ONE normalisation point for the whole system: whatever the project
            # sent becomes the standard vocabulary here, and every reader
            # downstream — panel, tools, API — just reads the columns.
            spec = roster_vocab.normalize(raw)
            name = spec["name"]
            if not name:
                continue
            agent = existing.get(name)
            if agent is None:
                session.add(
                    TrackerAgent(
                        project_id=project_id, name=name, role=spec["role"],
                        display_name=spec["display_name"],
                        kind=spec["kind"], model=spec["model"], tier=spec["tier"],
                        area=spec["area"], reports_to=spec["reports_to"],
                    )
                )
            else:
                # A refresh may correct anything, but must never blank a field
                # the manifest simply didn't mention this time.
                agent.display_name = spec["display_name"] or agent.display_name
                agent.role = spec["role"] or agent.role
                agent.model = spec["model"] or agent.model
                # A GUESS must never overwrite what is already stored — that
                # would undo a correction on the project's next check-in, and
                # `tier` always carries a value so it would happen every time.
                # Only what the manifest actually declared wins.
                declared = spec.get("declared") or {}
                if declared.get("tier") or not agent.tier:
                    agent.tier = spec["tier"]
                if declared.get("area") or not agent.area:
                    agent.area = spec["area"] or agent.area
                if declared.get("reports_to") or not agent.reports_to:
                    agent.reports_to = spec["reports_to"] or agent.reports_to
        await session.commit()
        return len(roster)


async def touch_project(project_id: int) -> None:
    """Record that this project just spoke to us (`last_seen_at`).

    Cheap enough to call on every inbound project call — it is what makes
    "project offline" an observable fact rather than a guess.
    """
    async with async_session() as session:
        project = await session.get(TrackerProject, project_id)
        if project is None:
            return
        project.last_seen_at = _now()
        await session.commit()


# ---------------------------------------------------------------------------
# Agents (a project's team roster)
# ---------------------------------------------------------------------------
async def create_agent(
    project_id: int,
    name: str,
    role: str | None = None,
    kind: str = "ai",
    model: str | None = None,
    tier: str | None = None,
    area: str | None = None,
    reports_to: str | None = None,
) -> TrackerAgent:
    """Add a team member to a project.

    Raises ValueError if that name already exists on the project — names are
    the join key to Directive activity, so they must be unique per project.

    `tier`, `area` and `reports_to` are the optional structure: where this
    member sits, which part of the project it works on, and who it reports to.
    They only shape how the fleet is drawn, and they go through the same
    normaliser the manifest path uses — so a member added by hand and one that
    arrived in a manifest are stored identically.
    """
    spec = roster_vocab.normalize({
        "name": name, "role": role, "kind": kind, "model": model,
        "tier": tier, "area": area, "reports_to": reports_to,
    })
    async with async_session() as session:
        existing = await session.scalar(
            select(TrackerAgent).where(
                TrackerAgent.project_id == project_id, TrackerAgent.name == name
            )
        )
        if existing is not None:
            raise ValueError(f"Agent '{name}' already exists on this project")

        agent = TrackerAgent(
            project_id=project_id, name=name, role=spec["role"],
            display_name=spec["display_name"], kind=spec["kind"],
            model=spec["model"], tier=spec["tier"], area=spec["area"],
            reports_to=spec["reports_to"],
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent


async def list_agents(project_id: int | None = None) -> list[TrackerAgent]:
    async with async_session() as session:
        stmt = select(TrackerAgent)
        if project_id is not None:
            stmt = stmt.where(TrackerAgent.project_id == project_id)
        stmt = stmt.order_by(TrackerAgent.id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Directives
# ---------------------------------------------------------------------------
async def create_directive(
    project_id: int,
    title: str,
    description: str | None = None,
    *,
    kind: str = "develop",
    priority: int = 100,
    task_id: str | None = None,
    parent_id: int | None = None,
    auto_merge: bool = False,
) -> TrackerDirective:
    async with async_session() as session:
        directive = TrackerDirective(
            project_id=project_id,
            title=title,
            description=description,
            kind=kind,
            status="queued",
            priority=priority,
            task_id=task_id,
            parent_id=parent_id,
            auto_merge=auto_merge,
            artifacts=[],
        )
        session.add(directive)
        await session.commit()
        await session.refresh(directive)
        return directive


async def create_children(
    parent_id: int, children: list[dict]
) -> list[TrackerDirective]:
    """Queue an epic's decomposition under it (architecture §7 case 13).

    Priorities ascend from the parent's, so the pieces run in the order the
    overseer chose and the whole epic still sits where the owner put it
    relative to other work. Idempotent by parent: a project retrying its report
    (case 9) must not queue the same decomposition twice, so an epic that
    already has children gets none added.

    WHY the parent is locked FOR UPDATE: the guard below is a count-then-insert,
    and the retry it exists to survive is exactly the one that can run
    CONCURRENTLY with the call it retries — infra/wardenkit retries PushReport
    on DEADLINE_EXCEEDED, which means the first handler may still be inside this
    function when the second arrives. Unlocked, both would count zero children
    and both would insert, and the epic would be decomposed twice. The lock
    serialises them, so the second one counts what the first one committed.
    """
    if not children:
        return []
    async with async_session() as session:
        parent = await session.scalar(
            select(TrackerDirective)
            .where(TrackerDirective.id == parent_id)
            .with_for_update()
        )
        if parent is None:
            return []
        already = await session.scalar(
            select(func.count(TrackerDirective.id)).where(
                TrackerDirective.parent_id == parent_id
            )
        )
        if already:
            logger.info(
                "Epic #%s already has %d children — ignoring a repeated decomposition",
                parent_id, already,
            )
            return []

        created = []
        for offset, child in enumerate(children, start=1):
            title = (child.get("title") or child.get("intent") or "").strip()[:500]
            if not title:
                continue
            # An epic never decomposes into epics, and an unlabelled piece is
            # `develop` — NOT the parent's kind.
            #
            # WHY: the only Directive that HAS children is an epic, so
            # inheriting the parent would make every unlabelled piece another
            # epic — dispatched, decomposed again, and again, with the Hub
            # cheerfully queueing the amplification. Nothing else in this system
            # bounds that depth, so it is bounded here. An unrecognised kind
            # falls back the same way instead of being stored: otherwise it
            # would be dispatched and rejected by the project minutes later, for
            # a typo the Hub could see now.
            kind = (child.get("kind") or "").strip()
            if kind not in DIRECTIVE_KINDS or kind == "epic":
                kind = "develop"
            directive = TrackerDirective(
                project_id=parent.project_id,
                title=title,
                description=child.get("intent") or None,
                kind=kind,
                status="queued",
                priority=parent.priority + offset,
                parent_id=parent.id,
                artifacts=[],
            )
            session.add(directive)
            created.append(directive)
        await session.commit()
        for directive in created:
            await session.refresh(directive)
        logger.info("Epic #%s decomposed into %d Directives", parent_id, len(created))
        return created


async def list_children(parent_id: int) -> list[TrackerDirective]:
    async with async_session() as session:
        result = await session.execute(
            select(TrackerDirective)
            .where(TrackerDirective.parent_id == parent_id)
            .order_by(TrackerDirective.priority, TrackerDirective.id)
        )
        return list(result.scalars().all())


async def reprioritise(project_id: int, ordered_ids: list[int]) -> int:
    """Rewrite the queue order for a project. Returns how many were moved.

    Only `queued` Directives are touched — reordering something already handed
    to a project would change nothing except the number the panel shows, and
    silently doing nothing is worse than saying so. Ids not belonging to this
    project are ignored, so a mistyped id can never move another project's work.
    """
    if not ordered_ids:
        return 0
    async with async_session() as session:
        moved = 0
        for position, directive_id in enumerate(ordered_ids, start=1):
            directive = await session.get(TrackerDirective, directive_id)
            if (
                directive is None
                or directive.project_id != project_id
                or directive.status != "queued"
            ):
                continue
            directive.priority = position
            moved += 1
        await session.commit()
        return moved


async def set_auto_merge(directive_id: int, value: bool = True) -> TrackerDirective | None:
    async with async_session() as session:
        directive = await session.get(TrackerDirective, directive_id)
        if directive is None:
            return None
        directive.auto_merge = value
        await session.commit()
        await session.refresh(directive)
        return directive


async def list_directives(
    project_id: int | None = None, status: str | None = None
) -> list[TrackerDirective]:
    async with async_session() as session:
        stmt = select(TrackerDirective)
        if project_id is not None:
            stmt = stmt.where(TrackerDirective.project_id == project_id)
        if status is not None:
            stmt = stmt.where(TrackerDirective.status == status)
        stmt = stmt.order_by(TrackerDirective.id.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_directive(directive_id: int) -> TrackerDirective | None:
    async with async_session() as session:
        return await session.get(TrackerDirective, directive_id)


async def claim_next(project_id: int, agent: str) -> TrackerDirective | None:
    """Atomically claim the next queued Directive for a project, or None.

    This is the POLLER tier's entry point (architecture §3.3) — the tier that
    doesn't run a Warden. The Hub never dials these projects, so they take work
    by asking.

    The UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)
    pattern is what guarantees two pollers never grab the same Directive: the
    inner SELECT locks exactly one queued row and any concurrent claimer skips
    it.

    WHY `dispatched` and not the v1 `claimed`: the v2 lifecycle has no
    `claimed` state — "someone has taken it, work has not been reported yet" is
    exactly what `dispatched` means, whichever tier took it.

    WHY no lease is opened here: pollers do not heartbeat, so a lease would
    have the sweeper requeue perfectly healthy long-running poller work. A NULL
    `lease_expires_at` means "nobody is on the hook by heartbeat" — the honest
    description of this tier.
    """
    async with async_session() as session:
        row = (
            await session.execute(
                text(
                    """
                    UPDATE tracker_directives
                    SET status = 'dispatched',
                        claimed_by = :agent,
                        updated_at = now()
                    WHERE id = (
                        SELECT id FROM tracker_directives
                        WHERE project_id = :pid AND status = 'queued'
                        ORDER BY priority, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id
                    """
                ),
                {"agent": agent, "pid": project_id},
            )
        ).first()
        await session.commit()

        if row is None:
            return None
        return await session.get(TrackerDirective, row[0])


async def report_directive(
    directive_id: int,
    project_id: int,
    status: str,
    summary: str | None = None,
    artifacts: list | None = None,
    error: str | None = None,
    task_id: str | None = None,
) -> TrackerDirective | None:
    """Update a Directive from its owning project (poller HTTP or Hub gRPC).

    Returns None if the Directive doesn't exist OR belongs to a different
    project — a project must never be able to report on another's work, and the
    two cases answer identically so a token cannot probe for the existence of
    someone else's Directives.

    Raises TransitionError when the status change is illegal.
    """
    async with async_session() as session:
        directive = await session.get(TrackerDirective, directive_id)
        if directive is None or directive.project_id != project_id:
            return None

        _apply_transition(directive, status)
        if status == "blocked":
            # A project REPORTING blocked has stopped: its pipeline hit
            # out-of-scope work and handed the decision back (architecture §7
            # case 6), so its heartbeat loop is already gone. Keeping the lease
            # would have the sweeper requeue it, the project re-run it, report
            # blocked again — forever, without the owner ever being asked.
            #
            # This is the OTHER blocked: the one the Hub sets itself while
            # holding an AskOwner call open keeps its lease, because there the
            # job really is alive and still beating. Same state, opposite
            # answer to "is anybody on the hook?", and who set it is what tells
            # them apart.
            directive.lease_expires_at = None
        if summary is not None:
            directive.summary = summary
        if artifacts is not None:
            directive.artifacts = artifacts
        if error is not None:
            directive.error = error
        if task_id:
            directive.task_id = task_id
        await session.commit()
        await session.refresh(directive)
        return directive


async def set_status(
    directive_id: int,
    status: str,
    *,
    error: str | None = None,
    summary: str | None = None,
    claimed_by: str | None = None,
    lease_seconds: int | None = None,
) -> TrackerDirective | None:
    """HUB-side status change (dispatch accepted, cancel, sweeper requeue).

    Same transition rules as a project's report — the Hub is not exempt from
    its own state machine; it just isn't scoped to one project.

    `lease_seconds` opens/extends the lease as part of the same transaction, so
    a Directive never exists in the window "dispatched but not yet leased"
    where a crash would strand it.
    """
    async with async_session() as session:
        directive = await session.get(TrackerDirective, directive_id)
        if directive is None:
            return None
        _apply_transition(directive, status)
        if error is not None:
            directive.error = error
        if summary is not None:
            directive.summary = summary
        if claimed_by is not None:
            directive.claimed_by = claimed_by
        if lease_seconds is not None and status in LEASED_STATUSES:
            directive.lease_expires_at = _now() + datetime.timedelta(seconds=lease_seconds)
        await session.commit()
        await session.refresh(directive)
        return directive


# ---------------------------------------------------------------------------
# Leases — the answer to "is anybody still working on this?"
# ---------------------------------------------------------------------------
async def touch_lease(
    directive_id: int, project_id: int, lease_seconds: int
) -> TrackerDirective | None:
    """Extend a Directive's lease (the Heartbeat path).

    Returns None when the Directive is unknown, belongs to another project, or
    is not in a leased state — a heartbeat for something already `done` is not
    an error worth failing the RPC over, but it must not resurrect the lease.
    """
    async with async_session() as session:
        directive = await session.get(TrackerDirective, directive_id)
        if directive is None or directive.project_id != project_id:
            return None
        if directive.status not in LEASED_STATUSES:
            return None
        directive.lease_expires_at = _now() + datetime.timedelta(seconds=lease_seconds)
        await session.commit()
        await session.refresh(directive)
        return directive


async def expired_leases(limit: int = 50) -> list[TrackerDirective]:
    """Directives whose holder stopped heartbeating — the sweeper's input.

    Only leased states are considered, and only rows that actually HAVE a lease
    (NULL = the poller tier, which never heartbeats and must never be swept).
    """
    async with async_session() as session:
        stmt = (
            select(TrackerDirective)
            .where(
                TrackerDirective.status.in_(LEASED_STATUSES),
                TrackerDirective.lease_expires_at.is_not(None),
                TrackerDirective.lease_expires_at < _now(),
            )
            .order_by(TrackerDirective.lease_expires_at)
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Mirrored agent Status — "who is doing what right now", across projects
# ---------------------------------------------------------------------------
async def upsert_agent_status(
    directive_id: int,
    agent_slug: str,
    state: str,
    *,
    role: str | None = None,
    progress: str | None = None,
    blockers: str | None = None,
    phase: str | None = None,
) -> TrackerAgentStatus:
    """Record this agent's latest Status for this Directive (one row per pair).

    Overwrites rather than appends — Status is observability, and the history
    that matters lives in the project's own Handoff files (architecture §1).
    """
    async with async_session() as session:
        row = await session.scalar(
            select(TrackerAgentStatus).where(
                TrackerAgentStatus.directive_id == directive_id,
                TrackerAgentStatus.agent_slug == agent_slug,
            )
        )
        if row is None:
            row = TrackerAgentStatus(directive_id=directive_id, agent_slug=agent_slug)
            session.add(row)
        row.state = state
        # Only overwrite what the update actually spoke about: a status that
        # omits `blockers` is silent about them, not clearing them.
        if role is not None:
            row.role = role
        if progress is not None:
            row.progress = progress
        if blockers is not None:
            row.blockers = blockers
        if phase is not None:
            row.phase = phase
        row.updated_at = _now()
        await session.commit()
        await session.refresh(row)
        return row


async def list_agent_status(directive_id: int) -> list[TrackerAgentStatus]:
    async with async_session() as session:
        result = await session.execute(
            select(TrackerAgentStatus)
            .where(TrackerAgentStatus.directive_id == directive_id)
            .order_by(TrackerAgentStatus.agent_slug)
        )
        return list(result.scalars().all())


async def record_agent_usage(
    directive_id: int,
    agent_slug: str,
    *,
    phase: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: float = 0.0,
) -> TrackerAgentUsage:
    """Append one persona turn's cost. Unlike `upsert_agent_status`, this never
    overwrites — see `TrackerAgentUsage`'s docstring for why it's a ledger."""
    async with async_session() as session:
        row = TrackerAgentUsage(
            directive_id=directive_id,
            agent_slug=agent_slug,
            phase=phase or None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost_usd,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def usage_totals(project_id: int | None = None) -> list[dict]:
    """Token/cost usage summed by (project, agent_slug). `project_id` in every
    row (not just a query filter) so the panel can cache one unscoped fetch
    and filter client-side per project, the same pattern `tasksCache` /
    `agentsCache` already use. What the Analytics tab renders — the data that
    answers "did the crew cost more than one agent would have" instead of a
    guess."""
    async with async_session() as session:
        query = (
            select(
                TrackerDirective.project_id,
                TrackerAgentUsage.agent_slug,
                func.sum(TrackerAgentUsage.input_tokens),
                func.sum(TrackerAgentUsage.output_tokens),
                func.sum(TrackerAgentUsage.cache_read_tokens),
                func.sum(TrackerAgentUsage.cache_write_tokens),
                func.sum(TrackerAgentUsage.cost_usd),
                func.count(TrackerAgentUsage.id),
            )
            .join(TrackerDirective, TrackerDirective.id == TrackerAgentUsage.directive_id)
            .group_by(TrackerDirective.project_id, TrackerAgentUsage.agent_slug)
        )
        if project_id is not None:
            query = query.where(TrackerDirective.project_id == project_id)
        result = await session.execute(query)
        return [
            {
                "project_id": pid,
                "agent_slug": slug,
                "input_tokens": int(inp or 0),
                "output_tokens": int(out or 0),
                "cache_read_tokens": int(cr or 0),
                "cache_write_tokens": int(cw or 0),
                "cost_usd": float(cost or 0.0),
                "turns": int(turns or 0),
            }
            for pid, slug, inp, out, cr, cw, cost, turns in result.all()
        ]


async def live_agent_status(project_id: int | None = None) -> list[tuple]:
    """Every agent Status attached to a Directive that is still in flight.

    Returns (status, directive, project) triples — one query, because
    `project_activity()` asks this across the whole estate and N+1 lookups per
    agent would make the answer arrive slower than the work it describes.
    """
    async with async_session() as session:
        stmt = (
            select(TrackerAgentStatus, TrackerDirective, TrackerProject)
            .join(TrackerDirective, TrackerAgentStatus.directive_id == TrackerDirective.id)
            .join(TrackerProject, TrackerDirective.project_id == TrackerProject.id)
            .where(TrackerDirective.status.notin_(TERMINAL_STATUSES))
            .order_by(TrackerProject.name, TrackerDirective.id, TrackerAgentStatus.agent_slug)
        )
        if project_id is not None:
            stmt = stmt.where(TrackerDirective.project_id == project_id)
        return list((await session.execute(stmt)).all())


# ---------------------------------------------------------------------------
# Questions — the durable half of a blocking AskOwner
# ---------------------------------------------------------------------------
async def create_question(
    directive_id: int,
    agent_slug: str,
    text_: str,
    suggested: list[str] | None = None,
    ttl_seconds: int | None = None,
) -> TrackerQuestion:
    async with async_session() as session:
        question = TrackerQuestion(
            directive_id=directive_id,
            agent_slug=agent_slug,
            text=text_,
            suggested=list(suggested or []),
            expires_at=(
                _now() + datetime.timedelta(seconds=ttl_seconds) if ttl_seconds else None
            ),
        )
        session.add(question)
        await session.commit()
        await session.refresh(question)
        return question


async def get_question(question_id: int) -> TrackerQuestion | None:
    async with async_session() as session:
        return await session.get(TrackerQuestion, question_id)


async def answer_question(question_id: int, answer: str) -> TrackerQuestion | None:
    """Record the owner's answer. Returns None if already answered — answering
    twice would be ambiguous for an agent that has already resumed on the first."""
    async with async_session() as session:
        question = await session.get(TrackerQuestion, question_id)
        if question is None or question.answered_at is not None:
            return None
        question.answer = answer
        question.answered_at = _now()
        await session.commit()
        await session.refresh(question)
        return question


async def pending_questions(project_id: int | None = None) -> list[tuple]:
    """Questions still worth answering, oldest first, with Directive and project.

    "Still worth answering" excludes the ones whose asker has already given up
    (`expires_at` in the past): the agent stopped listening and moved on, so
    putting them in front of the owner would invite an answer that reaches
    nobody. They stay in the table as a record of what was asked.

    The owner sees this list in Кая and in the panel; both need the context of
    what is being asked about, not just the question text.
    """
    async with async_session() as session:
        stmt = (
            select(TrackerQuestion, TrackerDirective, TrackerProject)
            .join(TrackerDirective, TrackerQuestion.directive_id == TrackerDirective.id)
            .join(TrackerProject, TrackerDirective.project_id == TrackerProject.id)
            .where(
                TrackerQuestion.answered_at.is_(None),
                or_(
                    TrackerQuestion.expires_at.is_(None),
                    TrackerQuestion.expires_at > _now(),
                ),
            )
            .order_by(TrackerQuestion.asked_at)
        )
        if project_id is not None:
            stmt = stmt.where(TrackerDirective.project_id == project_id)
        return list((await session.execute(stmt)).all())


async def dispatchable(project_id: int, limit: int) -> list[TrackerDirective]:
    """The next `limit` queued Directives for a project, best first.

    Ordered by (priority, id): lower priority runs first, and equal priorities
    keep FIFO order — so `reprioritise` only has to rewrite one column, and a
    Directive can never be starved by newer arrivals at the same priority.
    """
    if limit <= 0:
        return []
    async with async_session() as session:
        result = await session.execute(
            select(TrackerDirective)
            .where(
                TrackerDirective.project_id == project_id,
                TrackerDirective.status == "queued",
            )
            .order_by(TrackerDirective.priority, TrackerDirective.id)
            .limit(limit)
        )
        return list(result.scalars().all())


async def note_dispatch_failure(directive_id: int) -> int:
    """Record one failed hand-off attempt. Returns the new attempt count.

    Only the counter moves — the Directive stays `queued`, because a project
    that is merely down has not rejected anything. `updated_at` advances with
    it, which is what the dispatcher's backoff reads as "when we last tried".
    """
    async with async_session() as session:
        directive = await session.get(TrackerDirective, directive_id)
        if directive is None:
            return 0
        directive.dispatch_attempts = (directive.dispatch_attempts or 0) + 1
        directive.updated_at = _now()
        await session.commit()
        return directive.dispatch_attempts


async def set_task_id(directive_id: int, task_id: str) -> None:
    """Record the PROJECT-side task id the Warden assigned on accept.

    Worth storing the moment we learn it: it names the docs/tracker/{task_id}/
    tree, so it is how the owner finds the fleet's working files for a Directive
    — including for one that later fails without ever reporting anything else.
    """
    if not task_id:
        return
    async with async_session() as session:
        directive = await session.get(TrackerDirective, directive_id)
        if directive is None or directive.task_id == task_id:
            return
        directive.task_id = task_id[:255]
        await session.commit()


async def clear_dispatch_attempts(directive_id: int) -> None:
    """Forget the failed attempts once a project finally takes the work."""
    async with async_session() as session:
        directive = await session.get(TrackerDirective, directive_id)
        if directive is None or not directive.dispatch_attempts:
            return
        directive.dispatch_attempts = 0
        await session.commit()


async def leased_directives(project_id: int) -> list[TrackerDirective]:
    """Everything this project is supposed to be holding right now.

    Used to resync after a Hub restart: compare against what the Warden says it
    is actually running (architecture §7 case 9).

    A NULL lease is excluded for the same reason the sweeper excludes it —
    nobody is on the hook. That covers the poller tier, which never heartbeats,
    AND a Directive the project reported `blocked` and walked away from: the
    owner owes the next move there, and resyncing it back into the queue would
    take the decision away from them.
    """
    async with async_session() as session:
        result = await session.execute(
            select(TrackerDirective).where(
                TrackerDirective.project_id == project_id,
                TrackerDirective.status.in_(LEASED_STATUSES),
                TrackerDirective.lease_expires_at.is_not(None),
            )
        )
        return list(result.scalars().all())


async def count_in_flight(project_id: int) -> int:
    """How many Directives this project is ACTUALLY working on right now.

    "Working on" is narrower than "in a leased state", and the difference is a
    slot that never comes back:

      dispatched / running   always counted. Covers both tiers — a poller holds
                             work without a lease, and it is still holding it.
      blocked WITH a lease   counted. This is the AskOwner kind of blocked: the
                             job is alive, heartbeating, and occupying a worker
                             while it waits for the owner.
      blocked with NO lease  NOT counted. This is the case-6 kind: the project
                             reported out-of-scope work and stopped. Nothing is
                             running. Counting it would consume a slot for as
                             long as the owner takes to decide — and after
                             `max_concurrent` of them the project would be
                             permanently "full", silently dispatching nothing
                             ever again.

    The dispatcher compares this against `max_concurrent` before dialing, so a
    project at capacity is not even asked (case 3 remains the fallback for the
    race between this read and the Warden's own answer).
    """
    async with async_session() as session:
        result = await session.execute(
            select(func.count(TrackerDirective.id)).where(
                TrackerDirective.project_id == project_id,
                TrackerDirective.status.in_(LEASED_STATUSES),
                or_(
                    TrackerDirective.status != "blocked",
                    TrackerDirective.lease_expires_at.is_not(None),
                ),
            )
        )
        return int(result.scalar() or 0)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATUSES",
    "RegisterOutcome",
    "TransitionError",
    "answer_question",
    "approve_project",
    "claim_next",
    "clear_dispatch_attempts",
    "count_in_flight",
    "create_agent",
    "create_directive",
    "create_project",
    "create_question",
    "create_tables",
    "dispatchable",
    "expired_leases",
    "get_directive",
    "get_project",
    "get_project_by_name",
    "get_project_by_token",
    "get_question",
    "leased_directives",
    "list_agent_status",
    "list_agents",
    "list_directives",
    "list_projects",
    "live_agent_status",
    "note_dispatch_failure",
    "pending_questions",
    "register_project",
    "report_directive",
    "rotate_project_token",
    "set_project_state",
    "set_status",
    "set_task_id",
    "sync_roster",
    "touch_lease",
    "touch_project",
    "upsert_agent_status",
]
