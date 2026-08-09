---
description: Do a task, then loop reviewer agents over the result until no critical/high issues remain
argument-hint: <the task to perform>
---

# Agentic Review Loop

The user's task is:

<original_task>
$ARGUMENTS
</original_task>

Follow this workflow exactly. Do not skip the review phase even if you are confident in your work.

## Phase 1 — Do the work

Complete the task yourself, following all project conventions in CLAUDE.md
(WHAT/WHY/HOW module headers, prompts in `app/prompts/templates.py`, strict
tech stack). When you finish, note which files you created or modified —
the reviewer will need this list.

## Phase 2 — Review round

Launch a reviewer subagent with the Agent tool (`subagent_type: "general-purpose"`,
`run_in_background: false` — rounds must be sequential). The reviewer prompt MUST contain:

1. The ORIGINAL task, verbatim, copied from `<original_task>` above — the work
   can only be judged against what was actually asked, not against your summary of it.
2. The list of files that were created/modified (the reviewer reads them itself —
   do not paste file contents).
3. These reviewer instructions:
   - You are a strict, independent code reviewer. Read the changed files and
     check the work against the original task and the project's CLAUDE.md conventions.
   - Classify every problem you find by severity:
     - **critical** — the work is broken, wrong, or does not do what the task asked
       (bugs, crashes, missing core requirement, violates the strict tech stack)
     - **high** — a significant requirement of the task or of the project conventions
       is not met (missing WHAT/WHY/HOW header, prompt outside templates.py,
       missing error handling that will realistically fire)
     - **medium / low** — style, naming, minor improvements. Report only, do NOT fix.
   - Do NOT invent problems to seem useful — a clean result deserves an empty list.
   - **Fix every critical and high issue you find directly in the files.**
   - Return a report: for each issue — severity, file:line, what was wrong,
     and what you did about it (fixed / reported only).

## Phase 3 — Loop decision

- If the reviewer found **zero critical and zero high** issues → the loop is done, go to Phase 4.
- If the reviewer found (and fixed) any critical or high issues → start a NEW
  review round (Phase 2) with a FRESH subagent. The fresh reviewer gets the same
  original task and the updated file list, and reviews the current state of the
  code — including the previous reviewer's fixes, which may themselves contain mistakes.
- Hard cap: **5 review rounds**. If round 5 still finds critical/high issues,
  stop looping, keep the fixes made so far, and clearly tell the user which
  issues remain unresolved.

Track rounds explicitly ("Review round 2 of max 5...") so the user can follow progress.

## Phase 4 — Final report

Summarize for the user:
- What was built/changed in Phase 1 (files list)
- Per round: how many critical/high issues were found and fixed, one line each
- Remaining medium/low suggestions from the last round (not fixed, for the user to decide)
- Overall verdict: converged clean, or stopped at the round cap
