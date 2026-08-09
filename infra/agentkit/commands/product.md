# /product — Business / R&D / Devrel Intake ({{PROJECT}})

**You are the pipeline ORCHESTRATOR.** Do NOT decide anything yourself and do NOT
implement anything. Spawn each agent using the Agent tool. `subagent_type` =
filename in `.claude/agents/` without `.md`.

Use this command when **what is worth doing is not settled yet**:
business questions, R&D questions, devrel work, or a brand-new project.

**Do not use it for development work.** A feature, a bug or a refactor whose
*what* is already decided goes straight to the fleet:

| The ask | Command |
|---|---|
| "build X", "add Y to the API" | `/develop` |
| "Z is broken" | `/fix` |
| "clean up W" | `/refactor` |
| "break this epic into tasks" | `/epic` |
| "should we do X / what is it worth / what is the scope" | `/product` |
| "is this worth doing / which option serves users" | `/product` (R&D) |
| "which library / does this API support X / is it technically feasible" | `/research` — the architect frames and judges technical questions |
| "docs, an example, a demo, an announcement" | `/product` (devrel) |
| "start a new project from nothing" | `/product` (charter) |

If the ask turns out to be development work in disguise, **say so and stop** —
tell the project owner which command to use. Routing it through Ohno adds a hop
and a paraphrase, and paraphrase is where scope goes wrong.

---

## Before You Start

1. Assign a short `task-id` slug (e.g. `pricing-decision`, `rag-eval-rnd`,
   `quickstart-guide`).
2. Create tracker directories:
```bash
mkdir -p {{TRACKER_ROOT}}/{task-id}/status {{TRACKER_ROOT}}/{task-id}/tasks
```
3. Register the task so it shows up in the bot and dashboard:
```bash
TASK_RECORD=$({{TRACKER_CMD}} task:create \
  "product: {task-id}" \
  "{the ask, verbatim from the project owner}" \
  "product" \
  "Pipeline: /product")
TRACKER_ID=$(echo "$TASK_RECORD" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
{{TRACKER_CMD}} task:status "$TRACKER_ID" in_progress
```

---

## Phase 1 — Framing (Ohno)

Spawn sub-agent `product-owner-ohno` with:
```
Ask from the project owner: <full text>
task-id: <task-id>

Mode 2, Step 0 — classify this ask (business / R&D / devrel / new project) and
frame it. Do NOT plan the work yet.
- New project and no {{PRODUCT_ROOT}}/charter.md → you are in Mode 1: ask the nine
  charter questions instead.
- Otherwise: state the question, the options you can see, the non-goals, and what
  "done" must mean.
Ask everything you need in ONE batch via:
  {{ASK_OWNER_CMD}} "Ohno — <task-id>:\n<questions>"
Write your framing to {{TRACKER_ROOT}}/<task-id>/tasks/ohno-framing-<task-id>.md
Update {{TRACKER_ROOT}}/<task-id>/status/product-owner-ohno.yml
```

**After Ohno returns: STOP the pipeline.** Wait for the project owner's answers,
then continue.

---

## Phase 2 — Plan (Ohno, after the owner's answers)

Spawn `product-owner-ohno` with the owner's answers and:
```
task-id: <task-id>
Produce the deliverable for this type (Mode 2, Step 0 table):
- business → {{PRODUCT_ROOT}}/decisions/<slug>.md (question, options, recommendation,
  non-goals, what would change the answer)
- R&D      → a research brief for Curie at
             {{TRACKER_ROOT}}/<task-id>/tasks/ohno-to-curie-<task-id>.md:
             question, why now, what an answer must contain to be usable, budget
- devrel   → audience + outcome statement: who, what they can do afterwards,
             what is deliberately excluded
- new project → {{PRODUCT_ROOT}}/charter.md (approved first) then {{PRODUCT_ROOT}}/backlog.md
Send the plan to the owner for confirmation via {{NOTIFY_CMD}}.
```

**STOP. Wait for confirmation.** Changes requested → re-spawn Ohno, repeat.

---

## Phase 3 — Production (by type)

Ohno does not produce the artifact. Spawn the agent that does:

**R&D** (product-shaped only — a purely technical question belongs to
`/research`, where the architect frames and judges it) → `researcher-curie`:
```
task-id: <task-id>
Research brief: {{TRACKER_ROOT}}/<task-id>/tasks/ohno-to-curie-<task-id>.md
Answer every question in the brief with sources. Output path is in the brief.
```

**Devrel writing / specs** → `analyst-lovelace`; **visuals/mockups** →
`designer-davinci`, then `ui-reviewer-rams` — **only if those personas are
installed here.** If they are not, Ohno writes the artifact itself and says so in
the report. Never spawn an agent this project does not have, and never quietly
drop the deliverable because its usual author is missing.

**Anything that becomes code** → Ohno writes
`{{TRACKER_ROOT}}/<task-id>/tasks/ohno-to-xavier-<task-id>.md` first, then spawn
`architect-xavier` with that file as the request and run the `/develop` pipeline
from Phase 1 onward, unchanged. Acceptance criteria in that file are **fixed** —
Xavier may add technical criteria, never weaken one.

One item in flight at a time.

---

## Phase 4 — Acceptance (Ohno)

Spawn `product-owner-ohno` with:
```
task-id: <task-id>
Mode 4 — accept or reject. Trace each criterion to the evidence that proves it.
For code: each criterion needs an e2e scenario WITH its red-first proof.
For research: every brief question answered with sources, plus your verdict
(adopt / reject / revisit when X).
For devrel: someone other than the author followed it end to end.
Write {{TRACKER_ROOT}}/<task-id>/tasks/ohno-accept-<task-id>.md and report to the
owner, including what is NOT covered.
```

Not done → **one** revision round to whoever produced it. Same criterion failing
twice → stop and ask the owner with options and a recommendation.

---

## Phase 5 — Close

```bash
{{TRACKER_CMD}} task:status "$TRACKER_ID" done
{{NOTIFY_CMD}} "Ohno — <task-id>: <verdict>
<what was decided/found/shipped>
Not covered: <the honest gap>
Next: <follow-up task, or nothing>"
```

---

## Rules

- Ohno never writes implementation code, tests, or configs — if the answer is
  code, it goes to Xavier as a task with acceptance criteria.
- Ohno never re-words an acceptance criterion so that what was produced passes it.
- A rejected option, written down with the reason, is a delivered result. Do not
  treat "we decided not to" as a failed run.
- If any agent returns `blocked` — notify the project owner and stop.
- Budget: every item carries a ceiling. Exceeded → stop, report, ask. Ohno never
  raises its own ceiling.
