# Team Workflow — {{PROJECT}}

How the agent fleet coordinates work in this repository. Every persona in
`.claude/agents/` follows this document; where the two disagree, this document
wins and the persona is the bug.

{{SOLO_NOTE}}
---
## Zones
A **zone** is the unit of ownership: a set of paths, a rulebook, a verification
command, and exactly one owner at a time. Work that crosses zones is **split**,
never shared.

{{ZONE_TABLE}}

---
## Branch & merge model
### Iron rules (no exceptions)
1. Agents **never** push, merge or open PRs targeting `{{MAIN_BRANCH}}`.
2. Agents **never** run `git revert`, in any branch.
3. All PRs target `{{INTEGRATION_BRANCH}}`.
4. Before starting a task, sync the integration branch:
   ```bash
   git checkout {{INTEGRATION_BRANCH}} && git pull origin {{INTEGRATION_BRANCH}} && git merge origin/{{MAIN_BRANCH}} --no-edit
   ```
5. Before merging, the agent self-reviews its own PR diff and fixes what it finds.
6. After merging, report to the project owner via `{{NOTIFY_CMD}}`.
7. **Merge conflicts are never auto-resolved and never reverted.** Report and stop.
8. **Auto-merge is opt-in per task.** By default an agent opens a PR and waits. It
   may merge only when the owner says so for *that* task. There is no global
   auto-merge switch.

---
## Task ID and tracker directory
Each request gets a short `task-id`. Everything about it lives under
`{{TRACKER_ROOT}}/{task-id}/`:

- `tasks/*.md` — specs, findings, handoffs between agents
- `status/*.yml` — structured progress, one file per agent

Rules: every agent uses the same tracker root for the task, and **tracker
artifacts are never deleted** — not after completion, not after a cancellation.

---
## Agent registry
{{AGENT_REGISTRY}}

---
## Workflow phases
### Phase 0: Product intake ({{PO_NAME}}) — `/product` only
<!-- Renderer: delete this phase entirely if no Product Owner is installed. -->
**Skipped by every development command.** `/develop`, `/fix`, `/refactor` and
`/epic` start at Phase 1. Phase 0 exists for asks where *what is worth doing* is
still open: business questions, R&D, devrel, and a new project's charter.

Anything that turns into code leaves Phase 0 as a task, never as an
implementation: the PO writes `{{PO_HANDLE}}-to-xavier-{task-id}.md` with fixed
acceptance criteria, and Phase 1 runs unchanged.

{{PO_PHASE_NOTE}}

### Phase 1: Architecture & requirements (Xavier)
1. The project owner submits a request.
2. Xavier assigns a `task-id` and creates `{{TRACKER_ROOT}}/{task-id}/{status,tasks}/`.
3. Xavier reads the context ({{CONTEXT_DOCS}}) and the rulebooks for the affected
   zones, and decides which zones are involved.
4. Xavier asks clarifying questions — once, in one batch — and waits.
5. Xavier writes one spec per involved lead or direct-report developer, each
   naming the zone, the scope, the out-of-scope list, the contracts at the
   boundary, and the acceptance criteria.
6. Xavier opens a security review of the design for {{SECURITY_NAME}}.

### Phase 2: Security review of the design ({{SECURITY_NAME}})
Findings written to `{{TRACKER_ROOT}}/{task-id}/tasks/holmes-to-xavier-{task-id}-findings.md`.
Critical findings go back to Phase 1 before decomposition starts.

### Phase 3: Decomposition (leads)
<!-- Renderer: if this project has no leads, replace this phase with one line: specs go straight to the zone's developer. -->
Each lead decomposes its spec into **non-overlapping** developer tasks. Two
developers assigned the same file is the most expensive mistake available at this
step. Leads write tasks and acceptance criteria; leads do not write code.

Zones with a single developer skip this phase — the spec goes straight to them.

### Phase 4: Implementation (developers)
1. Developers implement only inside their zone.
2. Every rulebook named in their pre-read applies.
3. **Acceptance criteria are e2e scenarios**: one scenario per criterion, written
   before or alongside the code. Unit tests only where a specific piece of pure
   logic is worth pinning. A test that cannot fail is worse than no test.
4. Every new externally reachable surface goes in the developer's **surface
   audit** (what it is, who may reach it, how that is enforced, behaviour on
   invalid input).
5. Verification (the zone's own verify command — see the zone table above)
   passes **before** review.
6. Developers keep their own status file current.

### Phase 5: Code review loop (reviewers + leads, max {{DEVELOP_ROUND_CAP}} rounds)
Run the **review-loop** skill (`.claude/skills/review-loop/`) with the
original request as its task, a round cap of {{DEVELOP_ROUND_CAP}}, and
starting point **Already done** — the baseline is the ref recorded at the
start of `/develop`, since the implementation phases above already did the
work the skill would otherwise do in its own Phase 0-1. It owns the loop
mechanics from there — reading the diff, round caps, the `VERDICT:` line,
ping-pong detection, carrying medium/low findings across rounds — so this
phase only states what's specific to `/develop`, not the loop itself:

- Reviewers are scoped by zone and run in parallel when several zones changed.
- Leads verify scope compliance, ownership overlap and integration
  consistency, on top of what the skill's reviewer already checks.
- Order of checks: acceptance criteria first, then scope, then rulebooks,
  then a verification run (the zone's own — a failure is `high` regardless
  of what a status file claims), then correctness.
- **Reviewers report; the developer who owns the zone fixes** — the skill's
  "orchestrator only fixes" rule, applied per zone.

### Phase 6: Security review of the code ({{SECURITY_NAME}})
Traceability from the Phase 2 findings to the code that addresses them, then the
implementation itself: authz boundaries, input validation, secret handling,
logging, error leakage, and the surface audit against the diff.

### Phase 7: Completion (Xavier)
Xavier verifies that the sum of the changes matches the original request: every
criterion has a corresponding change, contracts are consistent, no zone was
edited by two owners, verification passes. This is the **technical** check.

### Reporting cadence and merge authority
How often the owner hears from the fleet, and whether it may merge its own PRs,
is one setting: the **autonomy level** in `.claude/git-workflow.md`
(L0 review each PR · L1 batch · L2 autonomous merge into
`{{INTEGRATION_BRANCH}}`). `{{MAIN_BRANCH}}` is never agent-merged at any level.

Batching lowers the noise, never the escalation: a blocked agent, a red gate, an
exhausted budget or a question for the owner goes out immediately at every level,
and stops the queue.

### Phase 8: Acceptance ({{PO_NAME}}) — only for work the PO dispatched
<!-- Renderer: delete this phase entirely if no Product Owner is installed. -->
Independent product-level accept/reject: each acceptance criterion traced to the
e2e scenario that proves it and the run result, **including the red-first proof**
(break the behaviour, watch the right assertion fail, restore). A criterion with
no scenario is not done. What the scenarios do not cover goes in the report.

A task that arrived through `/develop` without a PO is accepted by whoever sent
it; the PO does not review it uninvited.

---
## File conventions
### Product files (Phase 0 only)
- `{{PRODUCT_ROOT}}/charter.md` — what the project is and is not; changed only
  with the owner's approval.
- `{{PRODUCT_ROOT}}/backlog.md` — milestones → tasks, each with acceptance
  criteria as e2e scenarios, out-of-scope, `depends_on`, budget, status.
- `{{PRODUCT_ROOT}}/decisions/{slug}.md` — business decision memos.
Durable: they outlive any `task-id` and are never deleted. A cancelled task stays
with its reason — that is the record of a decision.

### Task files
`{{TRACKER_ROOT}}/{task-id}/tasks/{from}-to-{to}-{task-id}.md`, with `-fix-`,
`-refactor-`, `-review`, `-findings` variants. They describe **what** and the
acceptance criteria — never implementation code.

### Status files
`{{TRACKER_ROOT}}/{task-id}/status/{agent-slug}.yml`:
```yaml
agent: dev-anderson      # the FULL persona slug — same as the filename
role: dev
zone: {{ZONE_KEY_EXAMPLE}}
task: "Feature or sub-feature name"
state: in_progress
progress: "What is done so far"
blockers: null
updated_at: 2026-01-01T12:00:00Z
```
State values: `idle` / `pending` / `in_progress` / `blocked` / `review` / `done`.

**These field names are a contract, not a style.** A project connected to the
tracker relays these files upstream, and the panel joins them on the slug: write
`status:` instead of `state:`, or the short handle instead of the full slug, and
the agent shows as **idle forever** while it works. Update the file at every
transition — an agent that writes its status once, at the end, looks stalled for
as long as it is busy.

Developers moving to `review` must also carry `pre_read`, `verified` and
`surfaces` (see the dev persona).

---
## Fix flow (`/fix`)
1. Xavier assigns the bug to **one** zone.
2. The lead (or the direct-report developer) takes one fix task.
3. The developer fixes, verifies, and moves to review. Same gates as Phase 5–7,
   scoped to the change.

---
## Where the rules live
- **This file** — the procedure: who does what, in what order.
- **`.claude/projects/*.md`** — the project's law: invariants, per-zone rulebooks,
  the security checklist. Personas point at them; they never restate them.
- **Personas** — the role contract for one agent.

When a rule is stack-specific, it belongs in a rulebook, not in a persona. That
is what keeps the fleet reusable across projects.
