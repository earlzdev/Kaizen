# /abort — Discard Current Work & Return to Clean State

Use `/abort` to abandon the current task and return to a clean state.

---

## Step 1 — Identify current state

Check if there's an in-progress task in `{{TRACKER_ROOT}}/` status files.

If **git workflow instructions** were provided (remote mode):
```bash
CURRENT_BRANCH=$(git branch --show-current)
```
If already on `develop` or `main` → notify the project owner "Already on develop, nothing to abort." and stop.

---

## Step 2 — Update tracker (if applicable)

If a task-id can be identified:
```bash
{{TRACKER_CMD}} task:status "$TASK_ID" idle
{{TRACKER_CMD}} task:notes "$TASK_ID" "Aborted by the project owner. Returned to queue."
```

---

## Step 3 — Discard changes

If **git workflow instructions** were provided (remote mode):
```bash
git checkout -- .
git clean -fd
git checkout develop
git pull origin develop
git merge origin/main --no-edit
git branch -D "$CURRENT_BRANCH"
```

If **no git workflow instructions** (local mode):
- Revert any uncommitted changes in the working directory
- Report what was discarded

---

## Step 4 — Notify

If in remote mode:
```bash
{{NOTIFY_CMD}} "Aborted task branch: $CURRENT_BRANCH
Switched to develop. Ready for next command."
```

---

## Rules

- This command ONLY discards local changes. It does NOT delete remote branches or close PRs.
- If a PR was already created, notify the project owner so they can close it manually if needed.
- After abort, the task goes back to `idle` — it can be picked up again by `/next`.
