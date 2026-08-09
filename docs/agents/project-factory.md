# The project factory — idea → repo → deployed → built

<!--
WHAT: The architecture for turning "do a PoC of X" into a private repo, a
      deployed stack, a planned backlog and a fleet of agents that builds it —
      plus the ladder for getting there without building the whole thing first.
WHY:  the pieces mostly exist already (Hub = project registry, Warden = the
      project-side contract with AskOwner/PushReport, agent-pipeline = the dev
      fleet). What was missing was the layer that owns *what and why* per
      project, and the birth sequence that creates a project at all.
HOW to read it: §1 is the role split (the load-bearing decision). §2 is the
      honest inventory of have/missing. §3–§5 are the new pieces. §6 is the
      ladder — read it before building anything. §7 is what to build now.
STATUS: three pieces are now built — the PO persona (§3), the reusable agent
      kit at infra/agentkit/ (stack-free personas + a slot manifest), and the
      new-project skill that interviews, charters and scaffolds a repo with a
      tailored fleet (§4). What is NOT proven is a run through them: M0/M1 in §6
      are still ahead. The rest of this file is the design record.
-->

Companion to [`docs/e2e/README.md`](../e2e/README.md) (how work is proven) and
[`docs/reference/agent-pipeline/`](../reference/agent-pipeline/README.md) (how
work gets implemented). This document is about how a *project* comes into
existence and who decides what goes into it.

---

## 1. The role split

**Кая is a proxy, not a manager.** This is the load-bearing decision.

If Кая owns backlogs, her voice work and her project work compete for the same
context, and every project's state has to live in her head. Transport-only means
a project's PO keeps working when Кая is down, and the same PO is reachable from
the admin panel instead.

```
you ──Telegram──► Кая          proxy ONLY: voice, addressing, delivery
                    │
                    ▼
                  Brain         auth, routing, memory
                    │
                    ▼
              Tracker Hub       project registry + backlog queue + dispatch
                    │  gRPC warden.proto
                    │  Hub→Warden: Dispatch / Cancel / Describe / Health
                    │  Warden→Hub: Register / PushStatus / PushReport / AskOwner
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   banana-shop   project-B   project-C     each: own private repo, own stack
     Warden
        │
        ▼
    PO (Ohno)      owns the charter, the backlog, the acceptance criteria,
        │          and the conversation with you. One per project.
        ▼
   Architect → leads → devs → QA(e2e)      the existing agent-pipeline fleet
```

| Layer | Owns | Must NOT |
|---|---|---|
| **Кая** | voice, addressing ("which project?"), delivering questions and reports | plan, hold backlogs, decide scope |
| **Brain** | auth, routing, shared memory | know anything about projects |
| **Hub** | the project registry, the backlog queue, dispatch | make product decisions |
| **PO** (new) | charter, backlog, acceptance criteria, done/not-done, talking to you | write implementation code |
| **Architect + fleet** | *how* it gets built — phases 1–7 of the pipeline | change *what* is being built |

---

## 2. What already exists, and what does not

**Already have:**

- **Hub** — the multi-project registry and dispatcher.
- **`AskOwner`** in the Warden contract — "ask me when stuck", already first-class
  and blocking. This is the escalation channel; no new mechanism needed.
- **`PushReport`** — "tell me when it's done".
- **`Cancel`** — "stop, I've changed my mind".
- **`Dispatch`** — the work queue.
- **`infra/agentkit/`** — the dev fleet (architect, leads, devs, reviewers) with
  its phase model and git safety invariants, now **stack-free**: personas carry
  the procedure, `{{SLOT}}`s carry the project, and per-project rulebooks carry
  the stack. `MANIFEST.md` is the rendering contract.
  (`docs/reference/agent-pipeline/` keeps the Docker runtime + tracker service.)
- **`modules/tracker/example/dummy-project/`** — a project that already speaks the
  whole Warden contract; the reference implementation to copy.

**Was missing — three things, now built and unproven:**

1. **The PO layer** above the architect — [`product-owner-ohno.md`](../../infra/agentkit/agents/product-owner-ohno.md) (§3).
2. **A project template** — `infra/agentkit/` plus the scaffold steps in the
   skill (§4-B).
3. **The birth sequence** — the [`new-project` skill](../../.claude/skills/new-project/SKILL.md) (§4).

Everything here is composition of parts that exist. What none of it has yet is a
single end-to-end run — see the ladder in §6.

---

## 3. The PO layer

**Built:**
[`infra/agentkit/agents/product-owner-ohno.md`](../../infra/agentkit/agents/product-owner-ohno.md)
— Taiichi Ohno, Product Owner, in the kit next to the architect it sits above.
Five modes (charter → plan → dispatch → accept → unblock/steer), a WIP limit of
one task in flight, and the budget/stall/kill rules from §7.
`infra/agentkit/workflow.md` brackets the existing seven phases with
**Phase 0** (charter, backlog, dispatch) and **Phase 8** (acceptance), and
`architect-xavier.md` now knows to take its request — and its fixed acceptance
criteria — from `ohno-to-xavier-{task-id}.md` when that file exists. Unproven
until M1.

**The PO is not the front door.** It is entered only by `/product`
(`infra/agentkit/commands/product.md`), and only for asks where *what is worth doing* is
still open: business, R&D, devrel, and a new project's charter. Ordinary
development — `/develop`, `/fix`, `/refactor` — enters at Xavier and never waits
for a PO. A **new project is the exception that matters here**: from a charter
there is no settled backlog to route around the PO, so in the factory it owns the
whole thing until the project has a direction of its own. That is the same rule,
not a second one — it is just that in a brand-new repo, nothing is settled yet.

The pipeline's architect owns *how*. Nobody currently owns *what and why*. That
is the PO:

- the **charter** (§5) and any later changes to it
- the **backlog**: milestones → tasks, each with acceptance criteria
- **acceptance criteria are written as e2e scenarios** — see
  [`docs/e2e/README.md`](../e2e/README.md) §3. This is what stops a test being
  written afterwards to rationalise whatever got built.
- the **done / not-done call** against those criteria
- the **conversation with the owner** — questions out via `AskOwner`, direction in

**Run it on demand, not resident.** A PO's state is durable data (charter,
backlog, status files), not process state. Ten projects with ten idle agent
containers burns money and RAM for nothing. Spawn a PO session when there is
something to do — a dispatch, an owner message, a blocked task — the same way
`docs/reference/agent-pipeline/runtime/agent/agent_runner.py` already runs
`claude -p` on demand. One persona file to maintain, not N.

The PO sits **above** the architect in the roster and hands it a scoped goal. It
never writes implementation code, for the same reason the leads do not.

---

## 4. The birth sequence

Three phases, and the split between them is the point.

### Phase A — Charter (conversation; nothing is created)

A `founder` role interviews the owner through Кая and produces `charter.md`.
**Decided since: the founder is not a separate persona — it is Mode 1 of the PO**
(`product-owner-ohno.md`), the one mode that runs before a repo exists. One
persona to maintain, and the agent that writes the charter is the agent that will
later be held to it.

**Then the owner approves it before a single file exists.** This gate is the
cheapest thing in the whole system and it is what prevents eight hours of agents
building the wrong product. Non-negotiable.

### Phase B — Scaffold (mechanical; §4–§5 of the skill)

**Built** as [`.claude/skills/new-project/`](../../.claude/skills/new-project/SKILL.md):
interview in one batch → charter → **approval gate** → zones → roster → render
the fleet from `infra/agentkit/` → scaffold the repo. Outward-facing steps
(remote repo, Hub registration, deploy) are asked for separately — the charter
approval covers "build this", not "publish it".

- private repo from the template
- `infra/wardenkit` wired, project registered with the Hub
- `.claude/` — the pipeline export + the `/e2e` method
- `.e2e/profile.yml`
- compose stack (base closed + dev overlay), `Makefile` verbs
- deployed to the host, health-checked

**Mechanical steps must be deterministic.** An agent *runs* this script; it does
not improvise it. Improvised scaffolding is how you end up with eight projects
and eight different `make up`.

### Phase C — Backlog

The PO wakes in the new repo, reads the charter, produces milestones → tasks with
acceptance criteria as e2e scenarios, queues them in the Hub, and starts
dispatching to its architect.

Steady-state loop per task:

```
Hub dispatch → architect/fleet implement → /agentic-loop (static gate)
   → /e2e (behavioural gate) → PushReport → next task
   └── blocked at any point → AskOwner → you
```

---

## 5. The charter

Output of Phase A, lives in the new repo, is the PO's source of truth.

Intake questions the `founder` must get answered before writing it:

1. What is it, in one sentence a stranger would understand?
2. Who is it for, and what do they do with it?
3. What is explicitly **out** of scope for this PoC? (the most valuable answer)
4. What does "done" mean — what must work for you to call it a success?
5. What stack, or "you pick"?
6. Where does it deploy, and does it need a public URL?
7. What data does it hold, and does any of it need to be real?
8. Budget ceiling — tokens, time, or both.
9. What may agents decide alone, and what must come back to you?

The charter records the answers plus: non-goals, success criteria written as
e2e-shaped scenarios, and the budget. Everything the PO plans must trace back to
a line in it.

---

## 6. The ladder (build in this order)

The plumbing is not the risky part — most of it exists. The risky assumption is:
**can a PO plus a dev fleet carry a PoC to a working, e2e-verified state without
the owner babysitting?** Nobody knows yet.

So do **not** automate the birth sequence first. Automating it first produces a
machine that reliably produces broken projects.

| | Milestone | Proves |
|---|---|---|
| **M0** | Run `/new-project` for the banana shop once, by hand, watching every step | what the template got wrong — it was written before the first run, so treat its output as a draft to correct |
| **M1** | Register it with the Hub; PO + architect do **one** small task, proven by **one** `/e2e` scenario. Zero autonomy | Warden dispatch, the fleet, `/e2e`, `AskOwner` all work together |
| **M2** | The backlog loop: PO plans N tasks, works them one at a time, reports; owner can pull/steer/cancel. **Budgets + kill switch land here** | the fleet can sustain unattended work |
| **M3** | Automate the birth sequence (Phases A–C) | — you now know what the script does, because you did it twice |
| **M4** | Multi-project addressing in Кая | — |

---

## 7. Three calls, decided

**Deploy topology.** Same host, one compose project per project
(`-p banana-shop`), own logical DBs, a reverse proxy (Caddy/Traefik) giving each
a subdomain. **With a resource cap per project and a `stop` verb** — three PoC
stacks left running will starve the box that runs Кая and Brain, and it will
present as "Кая got slow", which is an expensive thing to debug.

**Addressing.** Кая holds a *current project* pointer: "switch to banana-shop",
then everything routes there; `AskOwner` questions arrive tagged with the project
name and answering in reply routes back. A Telegram supergroup with one topic per
project is strictly better, but the pointer needs no infrastructure and works
today.

**Governance — build before autonomy, not after.** Per-project token/time budget,
max iterations per task, a stall detector, and a kill switch reachable from
Telegram (`stop banana-shop`). An autonomous fleet holding an API key with no
ceiling is the one failure mode here that spends real money while you sleep. The
git invariants from the pipeline export (never `main`, never `revert`, never
auto-resolve a conflict) apply unchanged.

---

## 8. What is needed now

Small list. Everything else is M2 or later.

1. ~~**`project-template/`**~~ — **done**: `infra/agentkit/` + the scaffold steps
   in the skill. What it still needs is M0 to correct it — a template written
   before the first real run is a guess, and this one is.
2. ~~**`charter.md` template + the intake question list**~~ — **done**: the nine
   questions live in the PO persona (Mode 1) and in the skill's §1, which also
   adds the four questions that pick the fleet.
3. ~~**The PO persona**~~ — **done**, see §3.
4. ~~**The `/e2e` artifacts**~~ — **done**: `docs/e2e/command/e2e.md`,
   `docs/e2e/profile.template.yml`, `docs/e2e/SETUP.md`
   ([`docs/e2e/README.md`](../e2e/README.md) §10 items 1/2/4). Item 3
   (`host/e2e-run`, the remote-box/SSH path) stays deferred — Warden projects
   use the dind path instead, see `infra/agentkit/MANIFEST.md` "E2E".

## 9. Scoping the first run

A "simple B2C marketplace" is not a small PoC: auth, catalog, cart, checkout,
payments, admin. For the **first** run through this machine, scope the banana shop
down to something the fleet can plausibly finish — catalog + cart, fake payment —
so the run tests *the factory*, not the marketplace. Otherwise a failure is
ambiguous between "the machine is broken" and "the task was too big", which is the
one result that teaches nothing.

Scope up once the factory has produced one finished thing.

---

## 10. Open questions

- ~~Does the `founder` role live in Kaizen permanently, or is it a mode of the PO
  that runs before the repo exists?~~ **Decided: a mode of the PO** (§4-A).
- Where is the PO's checkout when it runs on demand — the project's own container,
  or a Hub-side workspace with the repo cloned?
- Does the charter live in the project repo, in the Hub's DB, or both (repo as
  source of truth, Hub as index)?
- How does a project get its LLM credentials — one shared key with per-project
  budget accounting, or a key per project?
- What happens to a finished project: stopped, archived, or left running?
