# /next — Pick Up Next Task from Tracker

Use `/next` to take the next pending task from the tracker and run the appropriate
pipeline.

**Autonomy level of this project: {{AUTONOMY_LEVEL}}** (see
`.claude/git-workflow.md`). It decides two things and nothing else:

| | L0 — review each PR | L1 — batch | L2 — autonomous merge |
|---|---|---|---|
| after the PR | stop, report, wait | keep going, merge nothing | merge if every gate is green, keep going |
| owner hears | every task | once, end of queue | once, end of queue |

At **L1/L2 you do not stop between tasks** — loop back to Step 1 and take the
next one. What you *do* stop for is unchanged at every level: a `blocked` agent,
a red gate, an exhausted budget, or a question for the owner. Those reach the
owner immediately.

---

## Step 1 — Prepare workspace

If **git workflow instructions** were provided (remote mode):
```bash
PREV_BRANCH=$(git branch --show-current)
git checkout develop
git pull origin develop
git merge origin/main --no-edit
```

If `$PREV_BRANCH` was a task branch (matches `task/*`):
```bash
git branch -D "$PREV_BRANCH"
git push origin --delete "$PREV_BRANCH" 2>/dev/null || true
```

Also mark the previous task as `done` in the tracker if it's still in `review`:
```bash
PREV_TASK_ID=$(echo "$PREV_BRANCH" | sed 's|^task/||')
{{TRACKER_CMD}} task:status "$PREV_TASK_ID" done
```

If **no git workflow instructions** (local mode): skip git operations, just proceed to Step 2.

---

## Step 2 — Check for pending commands first

```bash
{{TRACKER_CMD}} cmd:next
```

If a command exists, read it and decide:
- `next_task` → proceed to Step 3
- `btw` → the project owner sent a note while you were busy; acknowledge briefly via `{{NOTIFY_CMD}} "Noted: ..."`, ack the command, continue the task
- `update_task` / `free_text` → interpret the message and act on the current in-progress task instead of taking a new one; acknowledge command with `{{TRACKER_CMD}} cmd:ack <id> done` when handled

If no command exists, proceed to Step 3 anyway.

---

## Step 3 — Get next task

If `/next` was called with a specific `task-id` argument (e.g. `/next feature-b-stats`), use that task directly:
```bash
{{TRACKER_CMD}} task:get <task-id>
```

Otherwise pick the next pending task from the queue:
```bash
{{TRACKER_CMD}} task:next
```

If `error: no_pending_tasks` → notify the project owner via `{{NOTIFY_CMD}} "Queue is empty — no pending tasks."` and stop.

Note the task **id**, **type**, **title**, **description**, **notes**.

---

## Step 4 — Mark task as in_progress

```bash
{{TRACKER_CMD}} task:status <task-id> in_progress
```

---

## Step 5 — Create working branch (remote mode only)

If **git workflow instructions** were provided:

Branch naming: `task/<task-id>`

```bash
cd /workspace
git checkout -b task/<task-id>
```

Save branch name:
```bash
{{TRACKER_CMD}} task:branch <task-id> "task/<task-id>"
```

If **no git workflow instructions** (local mode): skip this step.

---

## Step 6 — Run appropriate pipeline

Based on task `type`:

| type | command |
|---|---|
| `feature` | `/develop <title>: <description>` |
| `bugfix` | `/fix <task-id> <description>` |
| `refactor` | `/refactor <description>` |
| `brainstorm` | `/brainstorm <title>: <description>` |

The pipeline runs all phases (architecture → implementation → review → completion).

---

## Step 7 — Verify build

After implementation is complete:

```bash
{{VERIFY_ALL_CMD}}    # or a single zone's command when only that zone changed
```

If build fails:
- Fix the issue
- Re-run verify-build
- Do NOT proceed to PR creation until build passes

---

## Step 8 — Deliver Results

If **git workflow instructions** were provided (remote mode):
- Follow the git workflow rules: push, create PR, self-review
- **L0** → report the PR and stop.
- **L1** → record the PR link, say nothing, continue.
- **L2** → check the six gates in `.claude/git-workflow.md`. All green: merge and
  continue. Any red: **stop the queue** and report — never merge past a red gate,
  never "merge and mention it".
- Update tracker: `{{TRACKER_CMD}} task:pr <task-id> "<pr-url>"`

If **no git workflow instructions** (local mode):
- All changes are in the working directory
- Report what was implemented and which files were changed

---

## Step 9 — Loop or report

**L0** → stop here. The owner runs `/next` again when ready.

**L1 / L2** → keep a running ledger for this batch: task id, one line of what it
did, the PR link, merged or not, and anything you deliberately left. Then:
- more pending tasks and nothing red → back to **Step 1**;
- queue empty, or the milestone's last task done → send **one** report:

```bash
{{NOTIFY_CMD}} "Batch complete — <N> tasks
<per task: id · one line · PR link · merged/open>
Not covered: <the honest gaps, gathered across the batch>
Spend: <budget used of the ceiling>
Next: <what is left in the queue, or nothing>"
```

**Stop the line** — anything below ends the batch immediately, with a report of
what was finished and what was not:
- a gate went red (review, security, verification, e2e, conflict);
- an agent is `blocked`, or asked the owner a question;
- the budget ceiling is reached;
- the same task failed twice.

Unattended chains compound: task 5 gets built on task 3's wrong assumption, and
by the time anyone looks it is no longer one revert. Stopping early is the whole
value of running a queue rather than a single task.

---

## Rules

- If blocked at any step → `{{TRACKER_CMD}} task:status <task-id> blocked` and
  notify the project owner **immediately**, whatever the autonomy level. Silence
  is for progress, never for questions or failures.
- L0: after a task is done, the owner runs `/next` again.
- Never raise your own autonomy level, and never merge to `{{MAIN_BRANCH}}`.
