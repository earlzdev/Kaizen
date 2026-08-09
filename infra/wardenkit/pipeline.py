# =============================================================================
# Persona turn — infra/wardenkit/pipeline.py
# =============================================================================
# WHAT: `run_persona_turn()` — the one shape a persona's work takes in every
#       topology: report `in_progress`, run the `claude` CLI as that persona,
#       report `done`/`failed` with the turn's token usage attached. It is the
#       status→run→status pattern `example/dummy-project/warden.py`'s
#       architect→dev→reviewer pipeline already repeats by hand, three times.
#
# WHY it exists (docs/tracker-architecture.md, "Scaling the fleet"): a
#       Directive's handler defaults to ONE call to this — Alfred does the
#       work, no handoff, no second persona to pay context for. Growing the
#       fleet is not an architecture change: `wardenkit` has no opinion on
#       agent count (`WardenServicer` only needs a `JobResult` back). Adding a
#       persona is one more call to this helper with a different `slug`, plus
#       a `job.handoff()` between them. This is deliberately the ONLY place a
#       persona turn happens, so usage capture (below) never has to be wired
#       in twice.
#
# WHY usage capture lives here and not in `ClaudeRunner` itself: `CliRun`
#       already carries `usage` (clirunner.py parses it off the CLI's `result`
#       event) — this function's only job is to make sure it always reaches
#       the Hub, by passing it to the closing `job.status()` call. A handler
#       that calls `ClaudeRunner.run()` directly instead of through here will
#       still work; it just won't show up in the Analytics tab.
#
# HOW:  run = await run_persona_turn(
#           job, runner, ALFRED, "implement the request, then review your own diff",
#           phase="implement", role="generalist",
#       )
#       if not run.ok:
#           return JobResult(state="failed", error=run.failure_summary())
# =============================================================================

from __future__ import annotations

from infra.wardenkit.clirunner import CliRun, ClaudeRunner
from infra.wardenkit.servicer import DirectiveJob


async def run_persona_turn(
    job: DirectiveJob,
    runner: ClaudeRunner,
    slug: str,
    prompt: str,
    *,
    phase: str,
    role: str = "",
    system: str = "",
    model: str = "",
    effort: str = "",
    in_progress_note: str = "working",
) -> CliRun:
    """Run one persona's turn and report it — status, work, status, usage.

    Never raises: a failed CLI run is a result (`run.ok is False`), same
    contract as `ClaudeRunner.run()` itself. The caller decides what a failed
    turn means for the Directive.
    """
    await job.status(slug, "in_progress", role=role, phase=phase, progress=in_progress_note)
    run = await runner.run(prompt, agent=slug, system=system, model=model, effort=effort)
    await job.status(
        slug,
        "done" if run.ok else "failed",
        role=role,
        phase=phase,
        progress=run.answer[:200] if run.ok else run.failure_summary(),
        usage=run.usage,
    )
    return run


__all__ = ["run_persona_turn"]
