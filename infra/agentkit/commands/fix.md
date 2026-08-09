# /fix — Bug Fix Pipeline ({{PROJECT}})

{{SOLO_NOTE}}
**You are the pipeline ORCHESTRATOR.** Do NOT fix anything yourself.
`subagent_type` = the filename in `.claude/agents/` without `.md`.

Lighter than `/develop`: one zone, one developer, one review pass. If the bug
turns out to need work in two zones, stop and run `/develop` instead — a "fix"
spanning zones is a feature wearing a disguise.

---

## Before you start

`/fix <task-id> <bug description>`

- **Existing `task-id`** → reuse `{{TRACKER_ROOT}}/{task-id}/`, so the fix sits
  next to the work that introduced it.
- **New bug** → assign a slug, create
  `{{TRACKER_ROOT}}/{task-id}/{status,tasks}/`, and write the reproduction at the
  task root:
```bash
cat > {{TRACKER_ROOT}}/{task-id}/README.md <<'EOF'
# {task-id}

**Type:** fix
**Symptom:** {what the user sees}
**Expected:** {what should happen}
**Reproduction:** {exact steps, or the failing scenario}
EOF
```

A bug without a reproduction is a report, not a task. Get one before spawning
anybody — including from the owner, if that is the only source.

---

## Phase 1 — Triage (Xavier)

Spawn `architect-xavier`:
```
task-id: <task-id>
Bug: <description + reproduction>

Read {{TRACKER_ROOT}}/<task-id>/ for existing context.
Assign this bug to exactly ONE zone. Do not propose the fix — assign ownership
and state the expected behaviour.
Write: {{TRACKER_ROOT}}/<task-id>/tasks/xavier-to-<target>-fix-<task-id>.md
```

If Xavier reports the defect spans zones → stop, tell the owner, suggest
`/develop`.

---

## Phase 2 — Fix (developer, or lead → developer)

Zone with a lead: spawn the lead to write the single developer task, then the
developer. Zone without: spawn the developer directly.
```
task-id: <task-id>
Fix your task: {{TRACKER_ROOT}}/<task-id>/tasks/<from>-to-<dev>-fix-<task-id>.md

Reproduce the bug first, then fix it. A fix you cannot show failing before and
passing after is not a verified fix.
Add the regression scenario that would have caught this.
```

---

## Phase 3 — Review (max {{FIX_ROUND_CAP}} rounds)

Record the baseline before Phase 2 (`git rev-parse HEAD`). Run the
**review-loop** skill (`.claude/skills/review-loop/`): task = the bug
report, round cap = {{FIX_ROUND_CAP}}, starting point = **Already done**
with that baseline, reviewer = the zone's reviewer persona. **The owning
developer fixes findings — never you, the
orchestrator** (the skill's generic "orchestrator fixes" rule does not apply
here; ownership belongs to whoever owns the zone). Beyond the skill's own
checks, this review must confirm: the reproduction now passes, a regression
scenario exists and CAN fail, the change is minimal and inside the zone, no
unrelated refactoring rode along. Still failing at the round cap, or a round
re-reporting an earlier fix — the skill stops and escalates; a bug fix that
needs that many rounds is not a bug fix, it is a design problem wearing one.

Spawn `security-holmes` **only** if the bug or its fix touches auth, input
handling, secrets or an externally reachable surface.

---

## Phase 4 — Verify

```bash
{{VERIFY_ALL_CMD}}
```
Green, plus the reproduction now passing. Then deliver as in `/develop` Phase 9.

---

## Rules

- One zone, one developer. Two zones → `/develop`.
- No opportunistic refactoring inside a fix — it hides the fix in the diff.
- The regression scenario is part of the fix, not a follow-up.
- `blocked` → notify the owner and stop.
