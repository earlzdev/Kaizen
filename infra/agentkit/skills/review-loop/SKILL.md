---
name: review-loop
description: Do a task, then loop an independent reviewer over the result until no critical/high issues remain. Use after any non-trivial change — a command's own review phase invokes this instead of re-describing the loop inline.
---

# review-loop — do the work, then verify it independently until it holds

<!--
WHAT: The one review-loop implementation this project's fleet shares. Every
      command that has a review phase (`{{DEVELOP_ROUND_CAP}}` rounds for
      `/develop`, `{{FIX_ROUND_CAP}}` for `/fix`/`/refactor`, ...) invokes
      THIS skill instead of re-describing round caps, the VERDICT line and
      ping-pong detection in its own body. That used to be prose duplicated
      per command; a wrong instruction in one copy silently diverged from
      the others, and nobody could tell which commands still had the bug.
WHY it exists as a project skill and not tracker/Warden logic: review
      rounds happen INSIDE one `claude -p` turn, orchestrated by the Agent
      tool spawning a second, independent instance to read the diff fresh —
      the Warden only ever sees one CLI call per Directive (§ wardenkit).
      This is fleet-internal choreography, invisible to the Hub.
HOW an invoker uses it: state the task, the round cap for THIS command, and
      whether the work is already done (a command's own review phase invokes
      this AFTER its implementation phase — this skill is not always
      entered fresh). See "Inputs" below.
-->

## Inputs (stated by whoever invokes this skill)

- **Task** — the original request, verbatim. If nothing was stated, STOP and
  ask what to do; never review nothing.
- **Round cap** — a number. If the invoker did not state one, STOP and ask
  for it rather than guessing — a command's own review phase always states
  one (`{{DEVELOP_ROUND_CAP}}` for `/develop`, `{{FIX_ROUND_CAP}}` for
  `/fix`/`/refactor`); an owner invoking this directly should say a number
  too.
- **Starting point** — one of:
  - **Fresh**: the work does not exist yet → start at Phase 0.
  - **Already done**: the invoker completed the work itself (e.g. a
    command's earlier phases) and hands you a baseline ref → skip Phase 0-1,
    start at Phase 2 using that ref as `<baseline>`.
- **Reviewer** (optional override) — who reviews, if the invoker wants
  something other than the default in "Who reviews" below (e.g. `/fix`
  naming the zone's specific reviewer persona).

Follow this workflow exactly. Reviewer and worker roles are separated: **the
reviewer only reports, you (the orchestrator) only fix.** A reviewer must
never edit files.

## Who reviews

- **Crew:** spawn the project's reviewer persona (`.claude/agents/
  reviewer-*.md` — check the roster in `CLAUDE.md`'s Fleet table) with the
  Agent tool. It has never seen this conversation, only the diff.
- **Solo:** spawn `reviewer-strict` (`.claude/agents/reviewer-strict.md`) —
  **not** a fresh instance of the developer persona (`alfred`). This is the
  one deliberate exception to solo's "one agent owns everything": a fresh
  `alfred` instance told "you're reviewing, not writing" is still carrying
  the full tool list `alfred` has everywhere else — one bad turn from
  silently patching what it was supposed to only report, and there is no
  harness boundary stopping it, only an instruction. `reviewer-strict` has
  no `Write`/`Edit` in its tool list at all: it is *structurally* unable to
  touch code, not merely asked not to. It is always installed for solo
  regardless of any other spec key — it is infrastructure this skill needs,
  not a roster choice the interview makes.

Either way, the reviewer has never seen this conversation, only the diff —
that is what makes it independent, on top of whichever mechanism enforces
"never edits."

## Severity ladder

The one definition every reviewer persona in this project cites instead of
restating — a second copy is how "missing acceptance criterion" ends up
Critical in one file and High in another for no reason:

- **Critical** — wrong behaviour that breaks the feature, a missing
  acceptance criterion, or a security hole.
- **High** — real and blocking, short of critical: scope violation, rule
  violation, a failed verification run.
- **Medium** — real but non-blocking.
- **Low** — taste. Keep these few; a review that is mostly Low findings gets
  ignored, including its Criticals and Highs.

Only Critical/High are counted in the `VERDICT:` line and block the loop;
Medium/Low accumulate in the report body (Phase 3's carry-over).

## Phase 0 — Baseline checkpoint

Record the current git state so every later change is a reviewable diff:
`git add -A && git stash create` (or note the current `HEAD` if the tree is
clean). Remember this as `<baseline>`. If the repo has uncommitted unrelated
changes, say so before proceeding.

## Phase 1 — Do the work

Complete the task, following `CLAUDE.md`, the zone's rulebook, and
`.claude/git-workflow.md`.

**Self-gate before any review round** — cheap checks first, never burn a
review round on a missing import:
1. Run the changed zone's own verify command (see the zone table in
   `CLAUDE.md`), or `{{VERIFY_ALL_CMD}}` if the change touched more than one
   zone.
2. Fix every failure yourself.
3. Only when green: commit a checkpoint (`review-loop: phase 1 complete`),
   note the changed files, and proceed to Phase 2.

## Phase 2 — Review round N (of the round cap)

Announce the round: "Review round N of max `<cap>`." Spawn the reviewer (see
"Who reviews" above). The reviewer's prompt MUST contain:

1. The original task, verbatim — never a paraphrase.
2. The diff range to focus on: `git diff <baseline>..HEAD`. The reviewer
   reads files itself; do not paste file contents into the prompt.
3. The instruction to **run the project's own verification itself** (the
   changed zone's verify command, or `{{VERIFY_ALL_CMD}}` for a multi-zone
   change) rather than trust any status file's claim — a failure is
   automatically `high` (or `critical` if it breaks the build
   project-wide). This is the cheapest finding a reviewer can make and the
   one most often skipped.
4. For round ≥ 2: the previous round's full issue report, labeled
   "previously found and supposedly fixed — verify each fix independently
   before your own review."
5. The instruction that the report must end with exactly one line:
   `VERDICT: CLEAN` or `VERDICT: <n> critical, <m> high`.

## Phase 3 — Fix and loop decision

**Fixing (orchestrator only):** fix every `critical`/`high` issue with a
minimal diff — no unrelated refactoring. If you disagree with a finding,
leave the code as-is, note the disagreement, and flag it in Phase 4 — never
silently ignore it. Re-run the Phase 1 self-gate after fixing, then commit
(`review-loop: round N fixes`).

**Loop decision:**
- `VERDICT: CLEAN` (or `0 critical, 0 high`) → Phase 4. Converged.
- Otherwise → a NEW round (Phase 2) with a FRESH reviewer instance.
- **Ping-pong detection:** keep a running ledger of `file + one-line
  description` fingerprints across rounds. If round N re-reports an issue
  already fixed, or flags code that was itself written as a fix for a prior
  round's finding, that is reviewer disagreement, not progress — STOP
  immediately, keep the current state, and escalate both positions in
  Phase 4.
- **Round cap.** If the last round still finds critical/high issues, stop,
  keep every fix made so far, and list what's unresolved.

**Carry-over:** append every `medium`/`low` from each round to one
deduplicated list. A fresh reviewer will not re-find them all — nothing may
be lost between rounds.

## Phase 4 — Final report

To the owner (or the command that invoked this skill): what changed and the
checkpoint refs (`git diff <baseline>..HEAD`); one line per round (issues
found, what was fixed); the full accumulated medium/low list; any finding
you disagreed with, both positions stated; overall verdict — **converged
clean**, **stopped at the round cap** (with what's still open), or **stopped
on reviewer disagreement** (with both sides).
