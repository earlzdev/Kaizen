# How do I use git here — branches, commits, PRs?

<!--
WHAT: The git rules an agent must follow, including the ones that exist purely to
      keep an autonomous agent from doing something irreversible.
WHY:  an agent with push access and no rules will eventually force-push, revert,
      or auto-resolve a conflict wrong. These invariants are load-bearing.
STATUS: STUB — outline only. The authoritative source to condense is
      docs/reference/agent-pipeline/runtime/git-workflow.md and the "Branch &
      Merge Model" section of claude/workflow.md in the same export.
-->

## To fill in

- **Never push, merge, or open a PR against `main`.** PRs target `develop`.
- **Never run `git revert`**, on any branch.
- **Never auto-resolve a merge conflict.** Report it and stop.
- **Auto-merge is opt-in per task, explicitly** — there is no global switch.
- **Before starting**: sync `develop` from `main`. **Before merging**:
  self-review the PR diff and fix what you find.
- **Commit messages**: format, granularity, and what belongs in the body.
- **Commit/push only when asked.** Finishing a task is not permission to push.
- **What to do when the working tree is already dirty** when you arrive.

## Open

- Do all projects use the `main` + `develop` model, or only the ones running the
  full pipeline?
