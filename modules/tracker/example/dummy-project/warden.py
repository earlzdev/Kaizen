# =============================================================================
# Dummy project Warden — modules/tracker/example/dummy-project/warden.py
# =============================================================================
# WHAT: A project that speaks the whole tracker v2 contract and does no real
#       work. It enrolls, accepts Directives, writes Handoff files, pushes
#       agent Status, asks the owner a question, and reports a fake PR — with
#       no repo, no git, no `claude` CLI and no LLM anywhere.
#
# WHY it exists (docs/tracker-v2-plan.md, Step 7): it is a MANUAL EXERCISE
#       HARNESS, not a product service and not a test suite. Every failure it
#       produces is unambiguously a protocol bug. Once a real fleet is running,
#       a hang could be the overseer, a persona, a build, or the protocol — and
#       you would be debugging four layers at once. So this comes first.
#
# WHY the behaviour is driven by KEYWORDS IN THE INTENT: the 16 cases in
#       docs/tracker-architecture.md §7 have to be reachable by hand, from
#       Telegram or the panel, without editing and rebuilding this file between
#       each one. Say "hang" and it hangs; say "fail" and it fails. The
#       keywords are listed in the README and in HELP below.
#
# WHAT IT DEMONSTRATES for a real project: this file is the whole project side.
#       Everything fiddly — enrollment, heartbeats, the accept/at-capacity
#       answer, the Handoff filenames, cancellation — lives in infra/wardenkit,
#       so a real `warden.py` differs from this one only in what its handler
#       actually does.
#
# HOW: `docker compose -f modules/tracker/example/dummy-project/docker-compose.yml up -d`,
#      then approve it (Кая: "approve the dummy project", or the panel's Fleet
#      view), then send it Directives. See the README.
# =============================================================================

import asyncio
import logging
import os
import random

from infra.wardenkit import (
    CliUsage,
    DirectiveJob,
    HubClient,
    JobResult,
    WardenServicer,
    make_manifest,
    serve,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("dummy-warden")

HUB_ADDR = os.environ.get("HUB_ADDR", "tracker:9104")
NAME = os.environ.get("PROJECT_NAME", "dummy")
GRPC_ADDR = os.environ.get("GRPC_ADDR", "dummy-warden:9200")
BIND_ADDR = os.environ.get("BIND_ADDR", "0.0.0.0:9200")
REPO_ROOT = os.environ.get("REPO_ROOT", "/repo")
STATE = os.environ.get("STATE_DIR", "/state")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
# How long each fake pipeline phase takes. Short enough to watch, long enough
# to `docker kill` the container in the middle of one.
STEP = float(os.environ.get("STEP_SECONDS", "6"))

HELP = """intent keywords this dummy project understands:
  hang    — never finishes (for cancel and lease-expiry walks)
  fail    — reports `failed` with a fake build-log tail
  block   — reports `blocked` (out-of-scope work) and stops
  noask   — skips the clarifying question
  quick   — skips straight to the report
  solo    — one persona does the whole Directive, no crew, no handoff"""

# The fake fleet. Only three of these actually do anything — the rest exist so
# the panel's Team tab has a real SHAPE to draw. A roster declares two optional
# things beyond identity: `area` (which part of the project) and `reports_to`
# (who is above it), and together they turn the flat list into the tree the
# fleet really is: architect → leads → devs, with reviewers to one side.
#
# Slugs mirror the agent kit's (infra/agentkit/agents/), so what you see here is
# what a real project's chart will look like.
PRODUCT_OWNER = "product-owner-ohno"
ARCHITECT = "architect-xavier"
DEV = "backend-dev-anderson"
REVIEWER = "code-reviewer-granger"
# The `solo` keyword's persona — no handoff, no crew. Tier `architect` because
# the roster vocabulary has no "does everything" tier of its own; what matters
# for the panel's org chart is that it reports to nobody and has nobody under
# it, same shape as ARCHITECT here.
ALFRED = "alfred"

ROSTER = [
    {"slug": ALFRED, "name": "Alfred Pennyworth", "role": "Overseer (solo)",
     "tier": "architect", "model": "opus"},
    # The Product Owner owns WHAT is built; the architect owns HOW. It is tier
    # `product` — above the architect, below the human owner the Hub seeds — and
    # NOT tier `owner`: `owner` means the person this work is for, and claiming
    # it here would both label an agent "human" and stop the real owner from
    # ever being seeded on the chart.
    {"slug": PRODUCT_OWNER, "name": "Taiichi Ohno", "role": "Product Owner",
     "tier": "product", "model": "opus"},
    {"slug": ARCHITECT, "name": "Charles Xavier", "role": "Solution Architect",
     "tier": "architect", "model": "opus", "reports_to": PRODUCT_OWNER},
    # Backend: a lead with two developers under them.
    {"slug": "backend-lead-tesla", "name": "Nikola Tesla", "role": "Backend Team Lead",
     "tier": "lead", "area": "backend", "model": "sonnet", "reports_to": ARCHITECT},
    {"slug": DEV, "name": "Thomas Anderson", "role": "Backend Developer 1",
     "tier": "developer", "area": "backend", "model": "opus",
     "reports_to": "backend-lead-tesla"},
    {"slug": "backend-dev-neumann", "name": "John von Neumann", "role": "Backend Developer 2",
     "tier": "developer", "area": "backend", "model": "opus",
     "reports_to": "backend-lead-tesla"},
    # Android: its own lead and two platform devs.
    {"slug": "android-lead-torvalds", "name": "Linus Torvalds", "role": "Android Team Lead",
     "tier": "lead", "area": "android", "model": "sonnet", "reports_to": ARCHITECT},
    {"slug": "android-dev-parker", "name": "Peter Parker", "role": "Android Developer 1",
     "tier": "developer", "area": "android", "model": "opus",
     "reports_to": "android-lead-torvalds"},
    {"slug": "android-dev-romanoff", "name": "Natasha Romanoff", "role": "Android Developer 2",
     "tier": "developer", "area": "android", "model": "opus",
     "reports_to": "android-lead-torvalds"},
    # Frontend.
    {"slug": "frontend-lead-stark", "name": "Tony Stark", "role": "Frontend Team Lead",
     "tier": "lead", "area": "frontend", "model": "sonnet", "reports_to": ARCHITECT},
    {"slug": "frontend-dev-wayne", "name": "Bruce Wayne", "role": "Frontend Developer 1",
     "tier": "developer", "area": "frontend", "model": "opus",
     "reports_to": "frontend-lead-stark"},
    {"slug": "frontend-dev-croft", "name": "Lara Croft", "role": "Frontend Developer 2",
     "tier": "developer", "area": "frontend", "model": "opus",
     "reports_to": "frontend-lead-stark"},
    # Reviewers serve every team, so they land in the cross-cutting band.
    {"slug": REVIEWER, "name": "Hermione Granger", "role": "Code Reviewer",
     "tier": "reviewer", "model": "opus"},
    {"slug": "security-holmes", "name": "Sherlock Holmes", "role": "Security Reviewer",
     "tier": "reviewer", "model": "opus"},
    # Declares NO tier and NO area — proof the Hub's normaliser places an
    # undeclared persona sensibly (reviewer, cross-cutting) instead of dropping it.
    {"slug": "ui-reviewer-rams", "name": "Dieter Rams", "role": "Design Reviewer",
     "model": "opus"},
]


def _wants(job: DirectiveJob, word: str) -> bool:
    return word in (job.intent or "").lower()


def _fake_usage(input_tokens: int, output_tokens: int) -> CliUsage:
    """This project has no real `claude` CLI to draw token counts from — it
    fakes plausible ones (±15%) so the panel's Analytics tab has data to
    render for both topologies from day one, without waiting on a real
    fleet (Step 8)."""
    jitter = lambda n: int(n * random.uniform(0.85, 1.15))
    inp, out = jitter(input_tokens), jitter(output_tokens)
    return CliUsage(
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=int(inp * 0.6),
        cost_usd=round((inp * 3 + out * 15) / 1_000_000, 4),
    )


async def run_pipeline(job: DirectiveJob) -> JobResult:
    """The whole 'pipeline': three agents, two handoffs, one question, a PR."""
    logger.info("=== #%s (%s) task '%s': %s", job.id, job.kind, job.task_id, job.intent)

    if job.kind == "epic":
        return await decompose(job)
    if job.kind == "review":
        return await self_review_and_merge(job)
    if _wants(job, "solo"):
        # The default shape a real project's warden.py should reach for
        # (docs/tracker-architecture.md, "Scaling the fleet"): one persona,
        # no handoff, no second context to pay for. Exercised here so both
        # topologies show up in the panel's Analytics tab before a real
        # fleet (Step 8) exists to compare them for real.
        return await solo_pipeline(job)

    # --- phase 1: the architect writes a TZ and hands it to the dev ---------
    await job.status(ARCHITECT, "in_progress", role="architect", phase="TZ",
                     progress="reading the request, writing the spec")
    await asyncio.sleep(STEP)
    job.handoff(ARCHITECT, DEV, f"""## Spec for `{job.task_id}`

The owner asked: {job.intent}

Do the obvious thing. This is a dummy project, so "the obvious thing" is
nothing at all — the point is that this file exists, is greppable, and survives
a container restart.
""")
    await job.status(ARCHITECT, "done", role="architect", phase="TZ",
                     progress=f"spec handed to {DEV}", usage=_fake_usage(900, 500))

    if _wants(job, "hang"):
        # Case 7 (cancel mid-flight) and case 8 (kill the Warden): both need
        # something that is genuinely still running when you act on it.
        await job.status(DEV, "in_progress", role="backend", phase="implementation",
                         progress="working... (and will keep working forever)")
        logger.info("#%s: hanging on purpose — cancel me or kill this container", job.id)
        await asyncio.Event().wait()

    # --- phase 2: the dev works, and may need the owner --------------------
    await job.status(DEV, "in_progress", role="backend", phase="implementation",
                     progress="writing the code")
    await asyncio.sleep(STEP)

    if not _wants(job, "noask") and not _wants(job, "quick"):
        # Case 4/5: the RPC below is held open by the Hub until the owner
        # answers or the question times out. `answered` is what tells them
        # apart — an empty answer is still an answer.
        await job.status(DEV, "blocked", role="backend", phase="implementation",
                         progress="needs a decision from the owner",
                         blockers="which approach?")
        answer = await job.ask(
            DEV,
            f"Для «{job.intent}» — делаем быстро и грязно или по-нормальному?",
            timeout_sec=int(os.environ.get("ASK_TIMEOUT", "120")),
            suggested=["быстро и грязно", "по-нормальному"],
        )
        if answer.answered:
            logger.info("#%s: the owner said %r", job.id, answer.answer)
            await job.status(DEV, "in_progress", role="backend", phase="implementation",
                             progress=f"owner said: {answer.answer}")
        else:
            # Case 5: never silently guess. Say what you assumed.
            logger.info("#%s: nobody answered — proceeding under a stated assumption", job.id)
            await job.status(DEV, "in_progress", role="backend", phase="implementation",
                             progress="no answer in time — proceeding 'по-нормальному'")
        await asyncio.sleep(STEP)

    if _wants(job, "fail"):
        # Case 11: a build that cannot be fixed reports failed with the log
        # tail, and never opens a PR.
        await job.status(DEV, "done", role="backend", phase="implementation",
                         progress="build failed")
        return JobResult(
            state="failed",
            summary="the build did not pass",
            error="FAKE BUILD LOG\n  ...\n  error: this project is a dummy, nothing compiles",
        )

    if _wants(job, "block"):
        # Case 6: out-of-scope work. Stop and let the owner decide.
        return JobResult(
            state="blocked",
            summary="this needs infrastructure changes nobody authorised — widen the scope?",
        )

    job.handoff(DEV, REVIEWER, f"""## Ready for review — `{job.task_id}`

Implemented nothing, as designed. Please review the nothing.
""")
    await job.status(DEV, "done", role="backend", phase="implementation",
                     progress=f"handed to {REVIEWER}", usage=_fake_usage(2200, 1400))

    # --- phase 3: review, then a PR the owner has to look at ---------------
    await job.status(REVIEWER, "in_progress", role="reviewer", phase="review",
                     progress="reviewing the diff")
    await asyncio.sleep(STEP)
    await job.status(REVIEWER, "done", role="reviewer", phase="review",
                     progress="looks fine (there is nothing in it)", usage=_fake_usage(1100, 400))

    return JobResult(
        state="review",
        summary=f"PR открыт для «{job.intent}» — посмотри и скажи, мержим ли.",
        artifacts=[{"type": "pr", "url": f"https://example.invalid/dummy/pull/{job.id}"}],
    )


async def solo_pipeline(job: DirectiveJob) -> JobResult:
    """The `solo` keyword: one persona does the whole Directive.

    No handoff, no second agent's context to build — this is what
    `run_persona_turn()` looks like used once, which is the fleet's default
    shape (see "Scaling the fleet" in docs/tracker-architecture.md). The fake
    usage below is deliberately close to the CREW pipeline's single most
    expensive phase (DEV, ~2200/1400) rather than the crew's total — the
    point this harness exists to make visible in the Analytics tab.
    """
    await job.status(ALFRED, "in_progress", role="overseer", phase="implement",
                     progress="reading the request, doing the obvious thing")
    await asyncio.sleep(STEP)
    await job.status(ALFRED, "done", role="overseer", phase="implement",
                     progress="done — nothing to hand off, nothing to review "
                              "that wasn't already checked in the same turn",
                     usage=_fake_usage(2000, 1300))
    return JobResult(
        state="review",
        summary=f"PR открыт для «{job.intent}» (solo) — посмотри и скажи, мержим ли.",
        artifacts=[{"type": "pr", "url": f"https://example.invalid/dummy/pull/{job.id}"}],
    )


async def self_review_and_merge(job: DirectiveJob) -> JobResult:
    """Case 12: the owner granted auto-merge on a Directive sitting in `review`.

    The Hub answered that by queueing THIS Directive — kind `review`, carrying
    the parent's task_id so the work lands in the same docs/tracker/ tree. It is
    the only path here that ends `done`: a PR nobody has merged yet is `review`,
    which means "waiting on the owner", and the fleet must never mark that done
    on its own.
    """
    await job.status(REVIEWER, "in_progress", role="reviewer", phase="merge",
                     progress="re-reading the diff before merging")
    await asyncio.sleep(STEP)
    job.handoff(REVIEWER, DEV, f"""## Merged — `{job.task_id}`

Self-reviewed and merged, because the owner granted auto-merge for this piece
of work. Nothing was actually merged: there is no repo.
""")
    await job.status(REVIEWER, "done", role="reviewer", phase="merge",
                     progress="merged")
    return JobResult(
        state="done",
        summary=f"Смержил PR по «{job.intent}».",
        artifacts=[{"type": "pr", "url": f"https://example.invalid/dummy/pull/{job.id}"}],
    )


async def decompose(job: DirectiveJob) -> JobResult:
    """Case 13: an epic comes back done, with the pieces it broke into.

    The children are REPORTED, not queued by us — a project may only ever be
    given work. The Hub queues them under this Directive, and the owner's
    queue, priorities and cancel keep working on them exactly as on anything
    else.
    """
    await job.status(ARCHITECT, "in_progress", role="architect", phase="decomposition",
                     progress="breaking the epic into pieces")
    await asyncio.sleep(STEP)
    pieces = [
        {"title": f"{job.intent} — часть {n}", "intent": f"{job.intent}: шаг {n}",
         "kind": "develop"}
        for n in (1, 2, 3)
    ]
    await job.status(ARCHITECT, "done", role="architect", phase="decomposition",
                     progress=f"{len(pieces)} pieces")
    return JobResult(
        state="done",
        summary=f"Разбил на {len(pieces)} задачи, они уже в очереди.",
        children=pieces,
    )


async def main() -> None:
    hub = HubClient(HUB_ADDR, token_path=f"{STATE}/hub_token")
    manifest = make_manifest(
        NAME,
        purpose="A dummy project for exercising the tracker end to end",
        description=HELP,
        kinds=["develop", "fix", "refactor", "epic", "review"],
        roster=ROSTER,
        max_concurrent=MAX_CONCURRENT,
        repo_url="https://example.invalid/dummy",
        grpc_addr=GRPC_ADDR,
    )

    # Serve BEFORE enrolling: the Hub may dispatch the moment the owner
    # approves, and a project that is registered but not yet listening looks
    # exactly like one that is down.
    servicer = WardenServicer(
        manifest, run_pipeline, hub=hub, repo_root=REPO_ROOT, heartbeat_seconds=10
    )
    server = await serve(servicer, BIND_ADDR)
    logger.info("Dummy Warden listening on %s, enrolling with %s", BIND_ADDR, HUB_ADDR)

    await hub.enroll(manifest, poll_seconds=5)
    logger.info("Enrolled as '%s'. Waiting for Directives.\n%s", NAME, HELP)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(main())
