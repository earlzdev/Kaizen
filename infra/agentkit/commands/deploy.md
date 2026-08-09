# /deploy — Ship to Prod ({{PROJECT}})

Opens (or, when explicitly told, merges) the PR that ships this project's own
`{{MAIN_BRANCH}}` into `{{DEPLOY_BRANCH}}` — the branch
`.github/workflows/deploy.yml`'s self-hosted runner (`{{DEPLOY_RUNNER_LABEL}}`)
watches to redeploy prod by running `{{DEPLOY_CMD}}`.

No persona spawn — this is a single `git`/`gh` sequence, not development
work. Run it directly.

## When to use
- the owner (directly, or via a Hub `deploy` Directive) says to ship or
  deploy the current state of `{{MAIN_BRANCH}}`
- merging an already-open deploy PR, but ONLY when told to in so many words

## Rules
- **Never merge without being told to explicitly.** Opening the PR and
  reporting its URL is the default outcome every time; merging is opt-in
  per call, same as every other irreversible action this fleet takes.
- **Never invent a `{{DEPLOY_BRANCH}}` history.** If it doesn't exist yet on
  the remote, create it from the current `{{MAIN_BRANCH}}` tip — that's a
  one-time bootstrap, not something to repeat.
- **If there's nothing to deploy, say so and stop.** No commits on
  `{{MAIN_BRANCH}}` since the last deploy is a normal outcome, not an error
  — check by content (`git log origin/{{DEPLOY_BRANCH}}..origin/{{MAIN_BRANCH}}`
  being empty), not by comparing SHAs: a merge commit gives
  `{{DEPLOY_BRANCH}}` a new SHA every time even when its content still
  matches `{{MAIN_BRANCH}}`.

## Workflow
1. `git fetch origin {{MAIN_BRANCH}} {{DEPLOY_BRANCH}}`.
2. If `origin/{{DEPLOY_BRANCH}}` doesn't exist: `git push origin
   origin/{{MAIN_BRANCH}}:refs/heads/{{DEPLOY_BRANCH}}`, report that it was
   created and stop (it now equals `{{MAIN_BRANCH}}` — nothing to deploy on
   this first run).
3. If `git log origin/{{DEPLOY_BRANCH}}..origin/{{MAIN_BRANCH}} --oneline` is
   empty, report "nothing to deploy" via {{NOTIFY_CMD}} and stop.
4. Otherwise, if no PR is already open from `{{MAIN_BRANCH}}` into
   `{{DEPLOY_BRANCH}}`: open one — `gh pr create --base {{DEPLOY_BRANCH}}
   --head {{MAIN_BRANCH}} --title "Deploy: {{MAIN_BRANCH}} -> {{DEPLOY_BRANCH}}"
   --body "$(git log origin/{{DEPLOY_BRANCH}}..origin/{{MAIN_BRANCH}} --oneline)"`.
5. Report the PR URL (new or already-open) via {{NOTIFY_CMD}}.
6. Only if the request explicitly says to merge: `gh pr merge --merge
   <number>`. This is what actually triggers the prod redeploy — say so in
   the report.

## Git credentials
{{GIT_CREDENTIALS}}

## Report
The PR URL (or "nothing to deploy" / "branch just created"), and whether it
was merged.
