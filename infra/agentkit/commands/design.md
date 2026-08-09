# /design — UI Mockup Pipeline ({{PROJECT}})

**You are the pipeline ORCHESTRATOR.** Do NOT design anything yourself.
`subagent_type` = the filename in `.claude/agents/` without `.md`.

Design-only pipeline: mockups in {{DESIGN_TOOL}}, reviewed against the design
system. **No code is written here.** If the ask needs code, this is `/develop`.

> Requires `designer-davinci` and `ui-reviewer-rams` to be installed. If this
> project has no UI or no design system, this command should not exist here.

Usage: `/design <what to create or fix>`
- `/design create the checkout flow mockups`
- `/design fix the design-system violations in the profile screens`
- `/design add the missing Dark variants for onboarding`

---

## Before you start

1. Assign a `task-id` (e.g. `design-checkout`, `design-profile-fix`).
2. Create `{{TRACKER_ROOT}}/{task-id}/{status,tasks}/`.
3. Record the goal:
```bash
cat > {{TRACKER_ROOT}}/{task-id}/README.md <<'EOF'
# {task-id}

**Type:** design
**Goal:** {which screens/flows, and why}
EOF
```

---

## Phase 1 — Brief (Xavier)

Spawn `architect-xavier`:
```
task-id: <task-id>
Design request: <description>

This is a DESIGN task, not a code task. Do not design anything yourself.
Produce a brief for {{DESIGNER_NAME}}:
- the user flow the mockups must cover, step by step
- the screens to create or edit (with their existing references, if editing)
- which design-system rules are at stake
- what is explicitly out of scope
Write: {{TRACKER_ROOT}}/<task-id>/tasks/xavier-to-davinci-<task-id>.md
```

If the flow itself is unclear, Xavier asks the owner **once, in one batch**,
before writing the brief. Mockups built on a guessed flow get thrown away.

---

## Phase 2 — Mockups (da Vinci)

Spawn `designer-davinci`:
```
task-id: <task-id>
Your task: {{TRACKER_ROOT}}/<task-id>/tasks/xavier-to-davinci-<task-id>.md
Complete the design-system pre-read before touching anything.
Run your full self-validation checklist before setting status: review.
Capture a screenshot of every screen in every theme.
```

---

## Phase 3 — Review (Rams)

Spawn `ui-reviewer-rams`:
```
task-id: <task-id>
Review the mockups from {{TRACKER_ROOT}}/<task-id>/status/designer-davinci.yml.
Check the project-specific design rules FIRST, then tokens, components, theme
parity, and structure — not only the screenshots.
Write findings to {{TRACKER_ROOT}}/<task-id>/tasks/rams-review-davinci-<task-id>.md
```

**Must Fix** findings → re-spawn da Vinci with them → re-run Phase 3. Re-review
covers the flagged items *and* what sits next to them; fixes move things.

---

## Phase 4 — Completion (Xavier)

Spawn `architect-xavier`:
```
task-id: <task-id>
Verify: every screen in the brief exists, the flow is complete end to end,
nothing outside the brief was designed, and Rams has approved.
```

---

## Phase 5 — Report

```bash
{{NOTIFY_CMD}} "Design ready — <task-id>
Screens: <list>
Flow: <one line>
Not covered: <what the brief excluded>
Source: <where the file/section lives>"
```

---

## Rules

- No code, in any zone. The mockups are the deliverable.
- Da Vinci never approves their own work; Rams never edits mockups.
- An agent may **never** update a visual baseline — that is the owner's call.
- A screenshot is evidence, never the pass/fail oracle: judge structure too.
