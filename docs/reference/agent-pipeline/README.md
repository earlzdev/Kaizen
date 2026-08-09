# AI Agent Pipeline — Template Export

This is a sanitized, reusable export of a Claude Code multi-agent development
pipeline: a fleet of named subagent personas, orchestrated by slash-command
"pipelines," that can implement features, fix bugs, review code, and design
UI mockups in a design tool — either interactively on your machine or
remotely via a Telegram bot + task tracker + trigger server running in
Docker.

> **The persona/command half of this export has moved to
> [`infra/agentkit/`](../../../infra/agentkit/MANIFEST.md)** and was rewritten to
> be stack-free: personas carry the procedure, `{{SLOT}}`s carry the project, and
> per-project rulebooks carry the stack. New projects get their fleet rendered
> from there by the [`new-project` skill](../../../.claude/skills/new-project/SKILL.md).
> **Edit the kit, not this file.** What remains here is the Docker runtime the
> kit assumes: the Telegram bot, the trigger server, the tracker service, and the
> git rules injected into remote runs.

**This was a template export.** Agent names, the example tech stack, file paths
and service names in the runtime docs below are illustrative — adapt them to your
own stack before using them for real.

---

## Big picture

A human **project owner** drives the pipeline by typing a slash command
(`/develop`, `/fix`, `/refactor`, …) either directly in Claude Code or via a
Telegram bot. That command spawns a chain of specialized subagents — an
architect, one or more team leads, several developers, and scoped code /
security reviewers — who read and write plain files (task specs, status
YAML) under a shared tracker directory to coordinate without stepping on
each other's work. When work is remote (Telegram-triggered), a small stack
of Docker services relays commands in and status/PR links back out:

```
you (Telegram) → bot → tracker (REST API + web dashboard) → agent-runner
   → `claude --dangerously-skip-permissions -p <prompt>` → git/GitHub (PR)
   → notify.sh → tracker → bot → you (Telegram)
```

Locally, you skip the bot/tracker/runner entirely and just run the slash
commands directly inside `claude` — the orchestrator (the architect
persona) still spawns the same subagent fleet.

---

## Directory map

```
(moved) → infra/agentkit/
  agents/*.md            Persona templates (stack-free, {{SLOT}}-parameterised)
  commands/*.md          Slash-command pipeline definitions
  workflow.md            Master reference: zones, branch model, registry, phases
  rulebooks/*.md         Per-project rulebook templates (where the stack goes)
  MANIFEST.md            Rendering contract: roster rules + slot dictionary
runtime/
  git-workflow.md      Git safety rules injected into remote/Telegram-triggered agent runs
  bot/                 Telegram bot (Python, python-telegram-bot)
  agent/                HTTP trigger server + container that runs the `claude` CLI on demand
  scripts/                Shell helpers agents call: notify, ask-user, tracker CLI, build verification, GHCR login
tracker/
  server.js, README.md, public/index.html, package.json
                          Node/Express task tracker + web dashboard — the hub between bot, agent-runner, and agents
Makefile                 docker compose wrapper: up/down/build/login/logs/etc.
```

### The fleet, the commands and the workflow → `infra/agentkit/`

The personas, the slash-command pipelines and the master workflow reference now
live in [`infra/agentkit/`](../../../infra/agentkit/MANIFEST.md), rewritten so
that one kit serves every project:

- **`agents/*.md`** — the roster (PO → architect → leads → devs → reviewers +
  security, research, analyst, design). Each is a full persona: identity, zone
  ownership, mandatory pre-reads, workflow steps, self-check, status conventions.
  Stack-specific rules are **not** in them by design.
- **`commands/*.md`** — `/develop`, `/fix`, `/refactor`, `/epic`, `/review`,
  `/research`, `/brainstorm`, `/doc`, `/next`, `/abort`, and `/product` (the only
  command that enters at the Product Owner).
- **`workflow.md`** — zones, branch model, agent registry, phases 0–8, file
  conventions.
- **`rulebooks/*.md`** — where a project's stack knowledge goes.
- **`MANIFEST.md`** — which agents a project gets, and what every slot means.

The pattern worth keeping, wherever you take it: one architect who owns scope and
asks before anything is built, leads who decompose non-overlapping tasks,
developers who never touch files outside their zone, reviewers scoped by zone
plus one security reviewer who checks everything — and named identities, because
"Anderson is blocked" reads better than "dev-2 is blocked" in a transcript.

### `runtime/git-workflow.md`

Git-specific rules that only apply to remote/Telegram-triggered runs (the
orchestrator appends this file's content to agent prompts in that mode).

### `runtime/bot/`

A Telegram bot (`bot.py`) that gives the project owner a menu-driven
interface: create tasks by type, view the queue, send PR feedback, reset
context. It talks to the tracker's REST API — it never spawns Claude
directly.

### `runtime/agent/`

`agent_runner.py` is a small HTTP server (`/trigger`, `/clear`, `/status`,
`/health`) that runs inside the same container as the `claude` CLI. It:
- Spawns `claude --dangerously-skip-permissions -p <prompt>` when the
  tracker sends it a command.
- Expands slash commands from the project's `.claude/commands/*.md` into the prompt text
  itself, because Claude's non-interactive mode (`-p`) doesn't resolve
  slash commands the way an interactive session does.
- Manages `~/.claude.json` (OAuth session) backup/restore so container
  restarts don't lose auth.

`entrypoint.sh` clones the repo into the workspace volume on first boot,
configures git credentials from `GH_TOKEN`, and sets up mobile build
prerequisites (adapt/remove if your stack differs). `Dockerfile` builds the
full toolchain image (example: a backend runtime, a mobile SDK, Node, gh
CLI — swap for your own stack).

### `runtime/scripts/`

- `notify.sh` — agent → Telegram (via tracker's `/api/notify`)
- `ask_user.sh` — agent asks the project owner a question and blocks until answered
- `tracker.sh` — CLI wrapper around the tracker's REST API (task/command operations)
- `verify-build.sh` — build verification (backend/mobile/frontend); genericized to a
  placeholder service list — **fill in your own service names**
- `setup-ghcr.sh` — one-time Docker/GHCR registry auth on a deploy host; genericized —
  **fill in your own registry user and env file path**

### `tracker/`

A small Node/Express service that is the hub of the whole system:
- REST API for tasks (create/list/reorder/update), commands (bot → agent),
  and notifications (agent → bot), plus an SSE `/api/events` stream.
- A web dashboard (`public/index.html`) to create/reorder tasks and watch
  live agent status without touching Telegram.
- Reads agent status from `docs/tracker/{task-id}/status/*.yml` (see
  below) to show live progress.

See `tracker/README.md` for the full API and script reference.

### `docs/tracker/{task-id}/` (not included — runtime data)

This export does **not** include actual task data, only the convention:
each task gets a short `task-id`; agents read/write
`docs/tracker/{task-id}/tasks/*.md` (specs, handoffs between agents) and
`docs/tracker/{task-id}/status/*.yml` (structured progress: `agent`,
`role`, `task`, `status`, `progress`, `blockers`, `updated`) as they work.
This is how agents coordinate without a shared chat history — set up the
same convention in your own repo.

---

## The pipeline pattern

0. **(`/product` only) The PO defines what and why.** For business, R&D,
   devrel or a brand-new project, Ohno frames the ask, writes the decision /
   brief / charter, and — for a new project — a backlog whose acceptance
   criteria are Given/When/Then e2e scenarios, handing the architect **one**
   task at a time. `/develop`, `/fix` and `/refactor` skip this phase
   entirely and start at step 1.
1. **Architect defines scope.** The project owner (or the PO) submits a request. The
   architect asks mandatory clarifying questions, waits for confirmation,
   then writes a Technical Specification (TZ) file per team lead — scope,
   contracts, acceptance criteria, explicit out-of-scope items — and
   triggers an architecture-level security review.
2. **Team leads decompose.** Each lead breaks their TZ into non-overlapping
   developer tasks (by service, by layer, or by platform). Leads never
   write implementation code.
3. **Developers implement**, strictly inside their assigned file scope,
   updating their own status file as they go and self-checking against a
   rule list (e.g. no hardcoded strings, no stubbed logic, auth/authz
   checks in place) before moving to review.
4. **Scoped code reviewers** review by domain (mobile vs. backend), running
   in parallel if both are touched.
5. **Security reviewer** checks both the architecture (before decomposition)
   and the final code (auth boundaries, input validation, secret handling).
6. **Completion check.** The architect verifies the sum of all changes
   matches the original request and acceptance criteria before the task is
   marked done.

Full detail, including status-file YAML schema and task file naming
conventions, is in `infra/agentkit/workflow.md`.

---

## Branch & PR safety rules

These are the load-bearing safety invariants — keep them even if you change
everything else:

1. **Agents never push, merge, or open PRs against `main`.** All PRs target
   a `develop` branch.
2. **Agents never run `git revert`**, in any branch.
3. **Merge conflicts are never auto-resolved.** The agent reports the
   conflict to the project owner and stops.
4. **Auto-merge is opt-in per task, explicitly.** By default an agent opens
   a PR and waits. It may only merge if the project owner explicitly says
   so (a specific phrase, or an `auto_merge: true` flag on the task) for
   *that* task — there is no global auto-merge switch.
5. Before starting work, agents sync `develop` from `main`; before merging,
   agents self-review their own PR diff and fix issues first.

See `runtime/git-workflow.md` and the "Branch & Merge Model" section of
`infra/agentkit/workflow.md` for the exact commands.

---

## Setup

1. **Environment variables** (see `tracker/README.md` and the Docker
   Compose files you'll add around this export):
   - `TELEGRAM_BOT_TOKEN` — create a bot via `@BotFather` on Telegram
   - `TELEGRAM_CHAT_ID` / `ALLOWED_CHAT_ID` — your chat ID (e.g. via
     `@userinfobot`); restricts the bot to you
   - `GH_TOKEN` — GitHub PAT with `repo` + `workflow` scopes, for the agent
     container to push/PR
   - `REPO_URL`, `GIT_USER_EMAIL`, `GIT_USER_NAME` — which repo the agent
     container clones and how it commits
   - `GPR_USER` / `GPR_TOKEN` — only needed if your build pulls private
     packages from a registry (e.g. GitHub Packages); omit otherwise
2. **Build the images**: `cd <this-dir-alongside-your-repo> && make build`
   (first build is slow if you keep the Android SDK layer — trim the
   Dockerfile to your actual stack).
3. **Authenticate Claude** inside the container: `make login` (opens an
   interactive OAuth flow) — or `make import-auth` to copy your local
   `~/.claude` session in for local dev.
4. **Start the stack**: `make up` — brings up `tracker`, `bot`, and
   `agent-runner`. Dashboard at `http://localhost:3333`.
5. Message your bot `/menu` on Telegram to confirm it's wired up.

See `Makefile` for the full command list (`logs`, `shell`, `ps`,
`fix-auth-perms`, `reset-workspace`, etc.) and `tracker/README.md` for the
REST API and local (non-Docker) usage.

---

## Adapting this template

- Choose the roster per project with the `new-project` skill rather than by hand; to change it for everyone, edit `infra/agentkit/agents/` to match your actual
  team shape (fewer or more developers, different platforms).
- Replace every `backend/<service>/...`, `mobile/...`, `frontend/...` path
  reference with your own repo's layout.
- Replace the placeholder service list in `runtime/scripts/verify-build.sh`
  with your real backend services (or delete the backend/mobile/frontend
  branches you don't need).
- Trim `runtime/agent/Dockerfile` to the toolchain your build actually
  needs (this example includes an example backend runtime + mobile SDK +
  Node — swap for your own stack).
- Write your own equivalent of `.claude/projects/*.md` — the "canonical
  invariants/rulebooks" that `infra/agentkit/workflow.md` and the agent personas
  reference (core invariants, backend rulebook, mobile rulebook, security
  checklist) — this export intentionally does not include the source
  project's originals since they're fully project-specific.
