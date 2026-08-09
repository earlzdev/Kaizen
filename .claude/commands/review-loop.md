---
description: Do a task, then loop independent reviewer agents over the result until no critical/high issues remain
argument-hint: <the task to perform>
---

# Agentic Review Loop v2

The user's task is:

<original_task>
$ARGUMENTS
</original_task>

If `<original_task>` is empty or is not an actionable task, STOP and ask the user
what to do. Do not review nothing.

Follow this workflow exactly. Do not skip the review phase even if you are
confident in your work. Roles are strictly separated: **reviewers only report,
you (the orchestrator) only fix.** A reviewer must never edit files.

## Phase 0 — Baseline checkpoint

Record the current git state so every later change is a reviewable diff:

```
git add -A && git stash create   # or note the current HEAD if the tree is clean
```

Remember the baseline ref. If the repo has uncommitted unrelated changes, tell
the user before proceeding.

## Phase 1 — Do the work

Complete the task yourself, following all project conventions in CLAUDE.md
(WHAT/WHY/HOW module headers, prompts in `app/prompts/templates.py`, strict
tech stack).

**Self-gate before any review round** (cheap checks first — never burn a review
round on a missing import):

1. Run the linter, the type checker, and the test suite (whatever the project
   defines; check CLAUDE.md / package scripts / Makefile).
2. Fix every failure yourself.
3. Only when the gate is green: commit a checkpoint
   (`git commit -m "review-loop: phase 1 complete"`), note the list of
   created/modified files, and proceed to Phase 2.

## Phase 2 — Review round N (of max 5)

Announce the round: "Review round N of max 5".

Launch the **strict-reviewer** agent (defined in
`.claude/agents/strict-reviewer.md`; if it does not exist, fall back to
`subagent_type: "general-purpose"` and inline its instructions).
`run_in_background: false` — rounds must be sequential.

The reviewer prompt MUST contain:

1. The ORIGINAL task, verbatim, copied from `<original_task>` above — never a
   paraphrase.
2. The list of changed files AND the diff range to focus on:
   `git diff <baseline>..HEAD`. The reviewer reads files itself — do not paste
   file contents.
3. For round ≥ 2: the previous round's full issue report, labeled
   "previously found and supposedly fixed — explicitly verify each fix before
   your independent review".
4. The instruction that its report must end with exactly one line:
   `VERDICT: CLEAN` or `VERDICT: <n> critical, <m> high`.

## Phase 3 — Fix and loop decision

Parse the reviewer's structured report (see agent file for the format).

**Fixing (orchestrator only):**
- Fix every `critical` and `high` issue with **minimal diffs** — no
  refactoring, renaming, or restructuring beyond what the issue requires.
- If you believe a reported issue is wrong, do not silently ignore it: note
  your disagreement, leave the code as is, and flag it for the user in Phase 4.
- Re-run the Phase 1 self-gate after fixing. Commit a checkpoint:
  `git commit -m "review-loop: round N fixes"`.

**Loop decision:**
- `VERDICT: CLEAN` → go to Phase 4 (converged).
- Otherwise → start a NEW round (Phase 2) with a FRESH reviewer.
- **Ping-pong detection:** keep a running ledger of issues as
  `file + one-line description` fingerprints across all rounds. If round N
  re-reports an issue already fixed in an earlier round, or flags code that was
  itself written as a fix for a previous round's issue, that is reviewer
  disagreement, not progress: STOP the loop immediately, keep the current
  state, and escalate both positions to the user in Phase 4.
- **Hard cap: 5 rounds.** If round 5 still finds critical/high issues, stop,
  keep all fixes made so far, and list the unresolved issues.

**Carry-over:** append every `medium`/`low` item from each round to an
accumulated, deduplicated list. Fresh reviewers will not re-find them all —
nothing may be lost between rounds.

## Phase 4 — Final report

Summarize for the user:

- What was built/changed in Phase 1 (file list), and the git checkpoint refs
  so any round can be inspected or reverted (`git diff <baseline>..HEAD`).
- Per round, one line: issues found (critical/high counts) and what was fixed.
- The full accumulated medium/low list from ALL rounds (not fixed — for the
  user to decide).
- Any reviewer findings you disagreed with and left unfixed, with both
  positions stated.
- Overall verdict: **converged clean**, **stopped at round cap** (with the
  open issues), or **stopped on reviewer disagreement** (with both sides).
  