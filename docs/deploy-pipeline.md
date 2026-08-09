# Deploy pipeline

<!--
WHAT: How "Кая, deploy your changes" turns into a running prod stack — the
      tool that opens the PR, the branch it targets, and the self-hosted
      GitHub Actions runner that redeploys on merge.
WHY:  written before a prod machine exists, so standing one up later is a
      documented checklist, not new design work.
HOW to read it: §1 is the flow end to end. §2 is one-time repo setup. §3 is
      standing up the runner on whatever machine becomes "prod".
-->

## 1. The flow

1. Owner tells Кая (Telegram): "deploy your changes" (or similar). Кая calls
   the `deploy` tool with `action=open_pr` (`tools/deploy/tool.py`) — it
   opens a PR from `main` into `deploy` via the GitHub REST API (no local
   git; see the tool's WHAT header) and reports the PR URL back in chat.
2. The owner reviews the PR on GitHub (or asks Кая about it). Nothing
   auto-merges — that's deliberate, see the tool's WHY.
3. When the owner says to merge, Кая calls `action=merge_pr`. Merging pushes
   to `deploy`.
4. `.github/workflows/deploy.yml` triggers on that push, runs on a
   self-hosted runner living on the prod host, and does `docker compose ...
   up -d --build` against the PROD overlay, then waits for every service's
   healthcheck.

## 2. One-time repo setup (do this once, not scripted — credentialed and
   destructive if done wrong)

1. Create the `deploy` branch on `origin` (`Kaizen-private`) if it doesn't
   exist yet — the `deploy` tool also creates it lazily on first use, from
   whatever `main` is at that moment.
2. Generate a fine-grained GitHub PAT scoped to **only** `Kaizen-private`,
   with repository permissions `Contents: Read and write` and
   `Pull requests: Read and write`. Put it in `.env` as `GH_DEPLOY_TOKEN`
   **and** set `GH_REPO=earlzdev/Kaizen-private` alongside it — neither has a
   built-in default (a hardcoded fallback would be wrong for anyone else
   running this codebase), and `tools/deploy/tool.py`'s `deploy()` refuses
   to run while either is empty (never commit `.env` — it's gitignored,
   `.env.example` documents the shape only).

## 3. Standing up a self-hosted runner on a prod machine

This is generic — repeat it for whatever host becomes "prod":

1. Install Docker + the Docker Compose plugin on the host.
2. Register a GitHub Actions self-hosted runner against `Kaizen-private`
   (repo → Settings → Actions → Runners → New self-hosted runner), running
   the setup commands GitHub shows you **on that host**. When it asks for
   labels, give it `kaizen-prod` — that's what `deploy.yml` targets
   (`runs-on: [self-hosted, kaizen-prod]`). This creates the runner's own
   working directory (usually `~/actions-runner/_work/...`) — that checkout,
   made fresh by `actions/checkout` on every run, is the **only** place the
   prod stack ever gets built from. Do not also `git clone` the repo
   somewhere else and `make up` from there: the compose project name is
   pinned to `kaizen` (`deploy/docker-compose.yml`'s `name:` field, not
   derived from the directory) and every service's `container_name:` is
   also fixed (`kaizen-brain`, …) — a second checkout would fight the
   runner's own for the same containers.
3. Put a real `.env` at `$HOME/kaizen-deploy.env` on that host **out of
   band** — scp it from your own machine, or fill it in directly. Never via
   git, never printed in a CI log (same rule as the "never read/print
   `.env`" rule everywhere else in this repo). It must live outside the
   runner's checkout: `actions/checkout` runs `git clean -ffdx` by default,
   which would delete a `.env` left inside it. `deploy.yml`'s first step,
   `Stage .env`, copies it in as `.env` at the checkout root on every run —
   required because every service's `env_file:` entry resolves relative to
   the compose file (i.e. always the checkout's own `.env`), so `--env-file`
   on the `docker compose` command line alone is not enough to get secrets
   into the containers.

   (Agentkit-scaffolded projects follow the same out-of-workspace
   convention, at `$HOME/{{PROJECT}}-deploy.env`, staged the same way — see
   `infra/agentkit/deploy-workflow.yml`.)
4. Install the runner as a service (`./svc.sh install && ./svc.sh start`)
   so it survives reboots and keeps listening for the next push to
   `deploy`.
5. First deploy: push anything to `deploy` (or merge a deploy PR) and watch
   the run under the repo's Actions tab.

A brand-new agent enrolling (Кая redeployed for the first time, say) still
needs a human `make approve` on the host — the workflow deliberately doesn't
run that step non-interactively (see `deploy.yml`'s WHY comment). That first
run's own "Verify every service is healthy" step will report the deploy as
FAILED even once you've approved it, because it only polls for up to ~6
minutes — run `make approve` on the host promptly after the first-ever
deploy, then re-run the workflow (or just push again) once the agent is
live.

## 4. Access

Brain is allow-by-default: any enrolled agent (not only Кая — e.g. Кузя,
once she's registered) can call the `deploy` tool, including
`action=merge_pr`. Nothing here restricts it beyond the tool description
telling the calling agent not to merge unsolicited — that's a prompt-level
guardrail, not an access-list one. If that's ever a concern, add a deny
`AccessRule` in `brain/access.py` scoping `tool="deploy"` to `kaya` only.
