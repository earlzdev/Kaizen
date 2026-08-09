# How do I start a new project from zero?

<!--
WHAT: The one command that turns an idea into a repo skeleton with its own agent
      fleet, and what it asks you along the way.
WHY:  doing this by hand produces a different `make up`, a different roster and a
      different idea of "done" every time. The judgement stays in the interview;
      the mechanics are identical in every project.
STATUS: written, unproven. Nothing has been created with it yet — treat the first
      run's output as a draft to correct, not as a finished skeleton.
-->

Run **`/new-project`** and answer the interview. That is the entry point.

## What it does, in order

1. **Interviews you once, in one batch** — nine product questions (what it is,
   who for, non-goals, what "done" means, stack, deploy, data, budget, autonomy)
   plus five that shape the fleet (which parts it has, repo name and location,
   whether you want the product layer, what agents must never do here, and
   whether it joins the Kaizen tracker).
2. **Writes a charter and stops.** Nothing is created until you approve it. The
   gate costs one message and prevents a fleet building the wrong thing overnight.
3. **Derives zones** — one per deployable part (`api`, `web`, `worker`, `infra`).
   A zone is a path set, a rulebook, a verify command, and exactly one owner.
4. **Picks only the agents that project needs**, by
   [`infra/agentkit/MANIFEST.md`](../../infra/agentkit/MANIFEST.md) §2: one dev
   per zone; a lead only when a zone has ≥2 devs or the project has ≥3 zones;
   designer + UI reviewer only with a UI *and* a design system; the PO only if you
   asked for it; architect and security always. **A 4–6 agent fleet is a good
   outcome** — a backend-only PoC has no business installing nineteen.
5. **Renders the fleet with a script, not by hand.** Your answers become a
   `project.json`; `infra/agentkit/render.py` turns it into
   `projects/<name>/` — the personas, the viable commands, the workflow, the
   deploy stack, every `{{SLOT}}` filled with that project's real paths and
   names — and then verifies it (no unresolved slots, no overlapping zone paths,
   no command spawning a persona that does not exist). It is idempotent: a
   change to the spec is one re-run, not a manual patch. Steps 4, 7 and 9 below
   describe what it produces, not what someone types.

   The default target is `projects/` inside Kaizen, which is a staging area —
   fine for the containerised fleet, wrong for an interactive session, which
   would read Kaizen's own `CLAUDE.md` as its own. Move the project out before
   working in it by hand.
6. **Authors the rulebooks** in `<project>/.claude/projects/` — this is where the
   stack lives: idioms, layout, interfaces, testing, traps.
7. **Scaffolds the repo**: git init (`main` + `develop`), zone directories,
   `README.md`, `CLAUDE.md`, two Makefiles with the same `up`/`down`/`logs`/
   `test` verbs — one at the root for the project's own app, one in
   `deploy/warden/` for the Warden dev-fleet (`cd deploy/warden && make up`) —
   `.e2e/profile.yml`, `docs/{tracker,product,specs}/`, `.env.example`
   committed and `.env` never (one pair of each per stack), first commit. The
   app's own `deploy/docker-compose.yml` is authored later, by the fleet, not
   by this step (MANIFEST.md) — once it exists it needs a matching
   `docker-compose.dev.yml`/`docker-compose.prod.yml` overlay pair, mirroring
   Kaizen's own root `Makefile`, or the app's `make up` refuses.
   **Published ports come from a per-project block bound to `127.0.0.1`, never a
   service's default** — `5432` on the host collides with Kaizen's own dev
   Postgres, and the bad case isn't a crash, it's an app quietly talking to
   another project's database. Inside the compose network services keep standard
   ports; only what's published has to be unique.
8. **Records the autonomy level** from your charter answer — L0 (a PR per task,
   you merge), L1 (agents work the queue and report once per milestone, merging
   nothing), or L2 (agents merge into `develop` when every gate is green, one
   report per milestone). `main` is never agent-merged at any level, and batching
   hides progress, never problems: a blocked agent, a red gate or an exhausted
   budget stops the queue and reaches you immediately.
9. **Sets up git and credentials**: `.claude/git-workflow.md` (iron rules —
   never `main`, never `revert`, never force-push, PRs onto `develop`,
   auto-merge opt-in per task), and, if the fleet is to push, a **repo-scoped
   fine-grained token** in `deploy/warden/.env` (`contents: write` +
   `pull_requests: write`) — the Warden's own env file, separate from the
   project root's `.env` for the app itself. Developers commit inside their
   zone; only the orchestrator pushes, once, at the end. The token is never
   printed, never read into an agent's context, and never lives anywhere but
   `deploy/warden/.env`. No token means Phase 9 reports a file list instead of
   a PR — stated up front, not discovered later.

   Two things are structural rather than asked-for: the container's entrypoint
   moves `GH_TOKEN` into `gh`'s own config and then drops it, so no agent can
   `printenv` it; and each `.env.example` is mounted over its real counterpart
   (`/repo/.env` and `/repo/deploy/warden/.env`), so what an agent sees when it
   opens either `.env` is the committed fake. See
   [`secrets-and-env.md`](secrets-and-env.md).
10. **Wires the Warden**, if you said the project joins the tracker — the
   project-side gRPC daemon built on `infra/wardenkit`, the only thing another
   repo imports from Kaizen. The Hub dials it; it pushes per-agent status, can
   block on `AskOwner`, and requeues its work if it crashes. Its manifest roster
   mirrors `.claude/agents/` one-to-one, because the persona filename *is* the
   join key for every status push.
11. **Asks separately before anything outward-facing** — creating the remote
    repo (private, using *your* `gh` auth, not the project's token), **enrolling
    the Warden** (you approve it deliberately with `make approve`), deploying.
    Approving the charter means "build this", not "publish it".

## What you get

```
<project>/
  .claude/
    agents/       only the personas this project needs
    commands/     /develop /fix /refactor /epic /review /research /doc /next …
    projects/     the rulebooks — where the stack knowledge is
    workflow.md   zones, branch model, registry, phases 0–8
  .e2e/profile.yml
  docs/{tracker,product,specs}/
  warden.py          only if it joins the tracker — the Hub's way in
  Makefile, README.md, CLAUDE.md, <zone dirs>
```

Then: `/product` if the PO was installed (it plans the backlog from the charter),
otherwise `/develop <first task>`.

## Why the personas are stack-free

One persona file, every project. The procedure — read the task, pre-read, build
inside your zone, verify, self-check, status, review — does not change between a
Go service and a SwiftUI app. The *rules* do. So the procedure lives in the
persona and the rules live in the rulebook, and fixing the procedure once reaches
every project instead of one.

The corollary matters when you edit things later: **a stack rule that ends up
inline in a persona is a bug.** Move it to the rulebook.

## Answers to what used to be open here

- **Template repo or generated?** Generated. The kit is `infra/agentkit/`; the
  skill renders from it, so improvements reach the next project instead of only
  the next clone.
- **Register with the tracker immediately?** No — only when there is work to
  dispatch, and only after you say yes. See
  [join-the-tracker.md](join-the-tracker.md).

## Kaizen-specific

- Edit the kit, never a project's rendered copy — the copy is output.
- `docs/reference/agent-pipeline/` now keeps only the Docker runtime (bot,
  trigger server, tracker service) that the kit assumes.
- Docker from the first commit, no local venv — see
  [run-and-verify.md](run-and-verify.md); secrets rules in
  [secrets-and-env.md](secrets-and-env.md); commit conventions in
  [git-and-prs.md](git-and-prs.md).
