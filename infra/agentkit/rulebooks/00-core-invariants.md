# Core invariants — {{PROJECT}}

<!--
WHAT: The rules that hold everywhere in this repository, in every zone.
WHY:  personas describe the procedure; this file describes THIS project. An agent
      that follows the procedure and breaks these has still broken the project.
HOW:  every developer, lead and reviewer pre-reads this file. Keep it short — a
      rulebook nobody finishes is a rulebook nobody follows. Under ~150 lines.
-->

> **Scaffolder:** this is a template. Fill each section from the interview
> answers. Delete a section rather than leaving it empty — an empty rule reads as
> "no rule" and gets treated as one. Add rules as the project teaches you them;
> a rule earned by a real bug is worth ten written up front.

## 1. Architecture invariants
{What must stay true about how the pieces fit: which layer may call which, what
must never import what, where state lives, what may cross a process boundary.}

## 2. Ownership
- Zones are defined in `.claude/workflow.md`. A developer edits only their zone.
- Cross-zone work is split into separate tasks — never shared files.
{Project-specific ownership rules: generated code, lockfiles, migrations, schema.}

## 3. Data and state
{Storage rules: what is the source of truth, what may be cached, what must be
idempotent, retention, what must never be stored at all.}

## 4. Error handling
{The project's error contract: how errors travel outward, what a caller can rely
on, what must never leak to a user or a log.}

## 5. Dependencies
{What may be added and by whom. Default: adding a dependency is a decision, not
an implementation detail — it goes back to the architect.}

## 6. Things that are never done here
{The list that saves the most time. Each entry: the rule, and one line on why —
a rule without a reason gets rationalised away at 3am by an agent in a hurry.}

- Never commit secrets, tokens or real personal data. Fixtures are fake.
- Never leave TODO/FIXME/stubbed logic in work handed to review.
- Never weaken a test to make it pass; fix the product or explain why the test
  was wrong.
