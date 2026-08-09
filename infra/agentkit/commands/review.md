# /review — PR Review Cycle ({{PROJECT}})

{{SOLO_NOTE}}
**You are the pipeline ORCHESTRATOR.** Do NOT implement fixes yourself.
Spawn developer agents using the Agent tool.

---

## Usage
```text
/review <the project owner's feedback>
```

Called after the project owner reviews the PR and finds issues to fix.

---

## Step 1 — Find the task under review

Find the most recent task with `status = review`:
```bash
{{TRACKER_CMD}} task:list
```

Note the `task-id` and `pr_url`. Check `{{TRACKER_ROOT}}/{task-id}/status/` to identify which developers worked on this task (look for status files with `status: review` or `status: done`).

---

## Step 2 — Spawn developers to fix feedback

Based on which developers have status files in `{{TRACKER_ROOT}}/{task-id}/status/`, determine who needs to apply fixes.

Spawn each responsible developer using the Agent tool.

For **each developer** that worked on this task:
```
task-id: <task-id>
PR review feedback from the project owner: <feedback>

Apply the requested fixes within your original scope.
Update your status file to: review.
```

If **git workflow instructions** were provided (remote mode), add to each developer prompt:
```
After fixing:
  git add -A
  git commit --amend --no-edit
  git push --force-with-lease origin task/<task-id>

Do NOT create a new PR. The existing PR updates automatically.
```

Spawn all relevant developers simultaneously.

---

## Step 3 — Notify the project owner

After all developers complete their fixes:

```bash
{{NOTIFY_CMD}} "PR updated: <task title>

Fixed: <brief summary of what was fixed>
Branch: task/<task-id>
PR: <pr-url> (same PR, updated)

Ready for re-review."
```

---

## Rules

- Each developer fixes only within their own scope (files they originally owned).
- If the project owner's feedback touches multiple developers' scopes, spawn all of them in parallel.
- If feedback is ambiguous about which developer owns the fix, default to the developer who owned the most relevant files.
- This cycle can repeat many times until the project owner is satisfied.
- In remote mode: NEVER create a new PR. Amend and force-push with `--force-with-lease`.
