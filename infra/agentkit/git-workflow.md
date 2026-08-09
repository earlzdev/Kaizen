# Git workflow — {{PROJECT}}

<!--
WHAT: How this project's agents branch, commit, push and open PRs — and how they
      are allowed to authenticate while doing it.
WHY:  an agent fleet with push rights is the one part of this system that can do
      damage nobody notices until later. The rules below are what keep a bad run
      recoverable: it can only ever produce a branch and a PR.
HOW:  read by the orchestrator before Phase 9 of any pipeline, and by any agent
      that touches git. Where this file and a persona disagree, this file wins.
-->

## Iron rules (no exceptions)

1. **Never push, merge or open a PR targeting `{{MAIN_BRANCH}}`.**
2. **Never run `git revert`**, in any branch.
3. **All PRs target `{{INTEGRATION_BRANCH}}`.**
4. **Merge conflicts are never auto-resolved and never reverted.** Report through
   `{{NOTIFY_CMD}}` and stop.
5. **Never `git push --force`**, and never rewrite published history.
6. **Never commit a secret.** `.env`, tokens, keys and real customer data stay
   out of every commit — including "temporarily, to test something".

These are recoverable-failure rules. A fleet that can only produce a branch and a
PR can be wrong all night and cost you one deleted branch.

## Branch flow

Before starting a task, sync the integration branch:
```bash
git checkout {{INTEGRATION_BRANCH}}
git pull origin {{INTEGRATION_BRANCH}}
git merge origin/{{MAIN_BRANCH}} --no-edit
```

Then work on a task branch:
```bash
git checkout -b task/{task-id}
```

## Who commits, who pushes

- **Developers commit inside their own zone.** Small, scoped commits, each
  message saying what changed and why.
- **Developers never push and never open PRs.** They implement and update their
  status file. This is what stops five agents racing to publish one branch.
- **Only the orchestrator (or {{ARCHITECT_NAME}} acting as it) pushes**, once,
  at Phase 9 — after review, security and verification have passed.

## Opening the PR

**Before pushing, commit everything about the task, not just zone code.**
Developers commit inside their own zone; tracker artifacts
(`{{TRACKER_ROOT}}/{task-id}/`) and any touched `{{PRODUCT_ROOT}}` files live
outside every zone and are nobody's job otherwise. `git status` and pick up
what is still untracked or modified before the push:
```bash
git add {{TRACKER_ROOT}}/{task-id}/
git commit -m "docs({task-id}): tracker artifacts" || true
```
(`git commit` exits non-zero with nothing staged — that's fine, it means a
developer's zone commit already covered everything.)

```bash
git push -u origin task/{task-id}
gh pr create --base {{INTEGRATION_BRANCH}} --head task/{task-id} \
  --title "{task-id}: {one line}" \
  --body  "{what changed, which criteria it satisfies, what is NOT covered}"
```

Then self-review the diff (`gh pr diff`) and fix what you find **before** asking
anyone to look at it.

## Merging — the autonomy level

This project runs at: **{{AUTONOMY_LEVEL}}**

| Level | The fleet | You hear from it |
|---|---|---|
| **L0 — review each PR** | opens a PR and stops | after every task |
| **L1 — batch** | opens PRs, keeps working the queue, merges **nothing** | once, at the end of the milestone, with every PR link |
| **L2 — autonomous merge** | merges its own PRs into `{{INTEGRATION_BRANCH}}` when every gate below is green, then continues | once, at the end of the milestone |

**`{{MAIN_BRANCH}}` is never agent-merged at any level.** Releasing
`{{INTEGRATION_BRANCH}}` into `{{MAIN_BRANCH}}` is a human action, always. That
single rule is what makes an unattended night recoverable: the worst case is a
`{{INTEGRATION_BRANCH}}` you reset.

### The gates for L2 — all of them, no exceptions
A PR may be merged by an agent only when **every** one is green:
1. the review loop converged on `VERDICT: CLEAN` (not "only nits left");
2. the security review is clean;
3. `{{VERIFY_ALL_CMD}}` passes;
4. every acceptance criterion has an e2e scenario **with its red-first proof**;
5. the PR merges cleanly — no conflict, no `--force`, nothing skipped;
6. the task is inside its budget.

Any one red → **do not merge, stop the queue, report**. Not "merge and note it".

```bash
gh pr merge <url> --merge --delete-branch
```

### Stop the line on the first failure
At L1 and L2 the fleet works a chain of tasks unattended, and **errors compound**:
task 5 is built on task 3's wrong assumption, and by the time you look, the fix
is not one revert. So a failed gate, a `blocked` agent, an exhausted budget or a
conflict stops the **whole queue**, not just the current task.

### Silence is for progress, never for problems
Batching means you are not told about each commit. It never means you are not
told about:
- a question an agent is blocked on (that reaches you immediately, always),
- a gate that went red,
- the budget running out,
- anything that needs a decision you did not already give.

An autonomy level lowers the noise, not the escalation.

## Credentials

{{GIT_CREDENTIALS}}

Rules that hold however the token is supplied:

- **The token is never printed, echoed, logged, pasted into a status file, or
  read out of `.env` into an agent's context.** Git and `gh` read it from the
  environment; an agent never needs to see its value, and a token that reaches a
  transcript has to be rotated.
- **It is scoped to this repository only**, with the smallest useful permission
  set: `contents: write` and `pull_requests: write`. Add `workflow: write` only
  if agents are expected to edit CI, and nothing else — never a classic
  organisation-wide token.
- **Missing token → stop and say so.** Never fall back to committing to a local
  branch and reporting success, and never try another account.
- Identity is set from `GIT_USER_NAME` / `GIT_USER_EMAIL`, so the history shows
  which agent fleet produced a commit.

## When there is no remote

A local-only project skips all of the above after the branch flow: changes stay
in the working tree, and Phase 9 reports which files changed instead of a PR
link. Do not create a remote to "finish properly" — that is the owner's call.
