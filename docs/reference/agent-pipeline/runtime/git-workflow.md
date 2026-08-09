# Git Workflow Rules (Remote Agent Mode)

These rules apply ONLY when working through the Telegram pipeline.
The orchestrator reads this file and appends it to agent prompts.

---

## Iron Rules (no exceptions)

1. **NEVER push, merge, or create PRs targeting `main`** — under any circumstances.
2. **NEVER run `git revert`** — in any branch.
3. **All PRs are created with `--base develop`** only.
4. **On merge conflicts** — do NOT auto-resolve, do NOT revert. Report to the project owner via `notify.sh` and WAIT.

---

## Before Starting Any Task

Sync `develop` from `main`:
```bash
git checkout develop && git pull origin develop && git merge origin/main --no-edit
```

Create a working branch:
```bash
git checkout -b task/<task-id>
```

---

## After Implementation — Push & Create PR

Only the pipeline orchestrator (Xavier) decides when to create the PR.
Individual developers do NOT push or create PRs — they implement code and update status files.

When all implementation and reviews are done:

```bash
cd /workspace
git push -u origin task/<task-id>
PR_URL=$(gh pr create \
  --title "<task title>" \
  --body "$(cat <<'EOF'
## Summary
<description of changes>

## Checklist
- [ ] Build verified
- [ ] Acceptance criteria met

Autonomous implementation — YourProject Dev Pipeline
EOF
)" --base develop)
```

Save PR to tracker:
```bash
tools/runtime/scripts/tracker.sh task:pr "$TRACKER_ID" "$PR_URL"
tools/runtime/scripts/tracker.sh task:status "$TRACKER_ID" review
```

---

## Self Code Review

Before notifying the project owner, review the PR diff:
```bash
gh pr diff "$PR_URL"
```

Check for:
- Bugs, security issues, code quality problems
- Leftover debug code, TODOs, hardcoded values
- Acceptance criteria compliance

If issues found — fix, push, re-review until clean.

---

## Auto-Merge

**Auto-merge is DISABLED by default. ALWAYS.**

The agent may ONLY merge a PR when the project owner has **explicitly granted permission** for that specific task.
Explicit permission means one of:
- the project owner wrote "merge yourself" / "you can merge it yourself" / "merge it"
- The task has `auto_merge: true` set by the project owner

**If permission was NOT explicitly given — DO NOT merge. Create the PR and wait.**

When auto-merge IS granted:
```bash
gh pr merge "$PR_URL" --merge --delete-branch
```

If merge fails (conflicts) — do NOT resolve, do NOT revert. Notify the project owner and wait.

After merge:
```bash
tools/runtime/scripts/tracker.sh task:status "$TRACKER_ID" done
tools/runtime/scripts/notify.sh "Merged: <task title>
PR: $PR_URL
Summary: <1-2 sentences>"
```

---

## If No Auto-Merge (default)

```bash
tools/runtime/scripts/notify.sh "PR ready for review: <task title>

Branch: task/<task-id>
PR: $PR_URL

Waiting for your review."
```

---

## Review Feedback (`/review`)

When the project owner sends review feedback on an existing PR:
- Developers amend within their scope
- Use `git push --force-with-lease` (never `--force`)
- Do NOT create new PRs — the existing one updates automatically

---

## Communication

Send replies to the project owner via:
```bash
tools/runtime/scripts/notify.sh "your message here"
```

This is the authorized communication channel (Telegram relay via tracker).
