# Tracker v2 — Implementation Plan

<!--
WHAT: Step-by-step rollout of the design in docs/tracker-architecture.md.
WHY step-numbered: same shape as the v1→v2 migration that worked (Steps 0–9).
     Each step below leaves the stack bootable and `make up`-healthy; nothing
     depends on a later step to run.
HOW to use: work top to bottom. Each step lists files, done-when, and how to
     verify in docker (there is no local venv — runtime is `make up` + `make logs`).
-->

Companion to `docs/tracker-architecture.md`. Vocabulary (Hub / Warden / Alfred /
Directive / Handoff / Status / Report / Question) is defined there, §1.

**Sizing:** S ≈ half a session, M ≈ one session, L ≈ multiple.

---

## Working constraints (owner's decisions)

**1. No tests for this feature yet.** Do not write unit, integration, or any
other automated tests for tracker v2 while it is being built. The schema, the
proto, and the tool surface are all still moving; tests written now would mostly
pin down shapes that are about to change. Existing tests in `tests/` stay green —
just don't add to them for this work.

> Consequence to accept knowingly: the two subtle parts of the tracker — the
> atomic claim (`store.py:168`) and the token boundaries — are refactored in
> Steps 1–4 with no automated net under them. Verification is manual (§ each
> step's *Done when*) plus the review loop below. Worth a "Step 10 — tests" once
> the contracts stop moving; noted at the end of this doc.

**2. Verify with the `agentic-loop` skill.** After the code for each step is
written, run `/agentic-loop` over the change and iterate until no critical or
high-severity issues remain. That loop is the quality gate in place of a test
suite for now — so it isn't optional here, it's the only gate. Treat a step as
incomplete until the loop comes back clean.

---

## Step 0 — Housekeeping · S

Nothing structural — clears the ground before the schema churn.

**Do:**
1. Move `modules/tracker/ai-agent-config-export/` → `docs/reference/agent-pipeline/`.
   It's 8k lines of Node + Markdown inside a Python service package — it pollutes
   the Docker build context and confuses the service-isolation rule.
2. Fix stale comment headers: `store.py`, `models.py`, `api.py` still say
   `app/tracker/...` and "its own two tables" (there are three).

**Done when:** `make up` healthy, tracker container logs clean, existing `pytest`
suite still green (nothing here should touch it).

---

## Step 1 — Schema and state machine · M

> ⚠️ **This step resets the tracker database.** No-migrations is a deliberate
> project decision, and `metadata.create_all` will not rename a table or add
> columns to an existing one. Pre-prod, so the honest move is to drop the
> `tracker` logical DB and let boot recreate it:
> `make psql` → `DROP DATABASE tracker;` → restart the tracker container.
> Do this knowingly, not by surprise. Other services' DBs are untouched.

**`modules/tracker/models.py`:**
- `TrackerTask` → `TrackerDirective`, table `tracker_directives`
  - add `kind`, `priority`, `task_id`, `parent_id`, `auto_merge`,
    `lease_expires_at`, `dispatch_attempts`
  - status set → `queued | dispatched | running | blocked | review | done | failed | cancelled`
- `TrackerProject` — add `purpose`, `description`, `state`
  (`pending|active|disabled`), `manifest` JSONB, `max_concurrent`, `grpc_addr`,
  `last_seen_at`
- **new** `TrackerAgentStatus` — directive_id, agent_slug, role, state, progress,
  blockers, phase, updated_at; unique on (directive_id, agent_slug)
- **new** `TrackerQuestion` — directive_id, agent_slug, text, answer, asked_at,
  answered_at

**`modules/tracker/store.py`:**
- `ALLOWED_TRANSITIONS: dict[str, set[str]]` + enforce it in the report path.
  Today `report_task` (`store.py:222`) will happily move a `done` Directive back
  to `running`, or `done` something never claimed.
- lease helpers: `touch_lease`, `expired_leases`
- keep `claim_next` unchanged — the poller tier still uses it

**Done when:** container boots and creates all five tables, `make logs` shows no
SQLAlchemy warnings, and the transition table has been walked by hand via
`make psql` (one legal path end-to-end, one illegal jump rejected). Then
`/agentic-loop` clean — the transition map is exactly the kind of thing a
reviewer catches and a booting container does not.

---

## Step 2 — Contract and project-side kit · M

**`infra/proto/warden.proto`** — both services from architecture §3, then
regenerate into `infra/proto/gen/` the same way `module.proto` is generated.

**`infra/wardenkit/`** — the shared project-side library. Mirrors
`infra/modkit`'s role: projects import this and nothing else from Kaizen.
- `WardenServicer` — implements `Dispatch/Cancel/Describe/Health`; takes a
  handler callable and a manifest
- `HubClient` — `register / push_status / push_report / ask_owner / heartbeat`,
  token in metadata, retry with backoff
- `trackerfiles.py` — the `docs/tracker/{task-id}/{tasks,status}` conventions:
  write a Handoff, write/read a Status YAML. One implementation, so every
  project's agents agree on the format.

**Done when:** a throwaway script serves a Warden and answers `Health`; a
throwaway client calls it. No Kaizen changes needed yet.

---

## Step 3 — Hub inbound gRPC · M

`modules/tracker/hub_grpc.py` — the `Hub` service, served on a new port
alongside the existing Module port and HTTP port. Three faces now; extend the
compose healthcheck.

- `Register` — first call with no token creates a `pending` project and returns
  `pending=true`; owner approval issues the token; later calls with a valid
  token refresh the manifest and `last_seen_at`
- `PushStatus` / `PushReport` — resolve token → project, reject any
  `directive_id` not owned by it (same guarantee `report_task` gives today)
- `Heartbeat` — extends `lease_expires_at`
- `AskOwner` — inserts a Question, sets the Directive `blocked`, then **holds the
  RPC open** polling for an answer until `timeout_sec`

**Watch out on `AskOwner`:** a long-lived unary RPC needs gRPC keepalive
configured on both ends, and a hard server-side ceiling (say 30 min) so a
crashed Warden can't pin a server thread forever. Returning an empty answer on
timeout is a *normal* outcome, not an error — Alfred handles it (architecture
§7, case 5).

**Done when:** a throwaway script (not a test — a scratch client you keep in the
scratchpad) walks enroll → approve → status → report by hand, and wrong-token /
wrong-project calls are seen to be rejected. Then `/agentic-loop` clean, with the
auth boundary called out as the thing to scrutinise.

---

## Step 4 — Hub outbound: dispatcher and sweeper · M

**`modules/tracker/dispatcher.py`** — dials Wardens.
- pick the next `queued` Directive by (priority, id), respecting the project's
  `max_concurrent`
- `Dispatch` → on `accepted` set `dispatched` + open a lease; on
  `reason=at_capacity` leave `queued`; on `reason=unsupported_kind` → `failed`
  with the reason surfaced to Кая
- unreachable Warden → `dispatch_attempts++`, exponential backoff, stay
  `queued`. Only tell the owner after N attempts — a container restart shouldn't
  generate a Telegram message.

**`modules/tracker/sweeper.py`** — expired lease → `queued` + re-dispatch.
Model it on Brain's reminder sweeper; same interval-loop shape.

**Done when:** with a dummy Warden you can watch accept, reject-at-capacity,
offline-retry, and `docker kill` mid-Directive → requeue → re-dispatch.

---

## Step 5 — Tool surface and the notification path · L

Two halves. This is the step where the loop finally closes.

**a) `modules/tracker/tools.py`** → the twelve tools from architecture §6.
`delegate_task` → `send_directive`, `task_status` → `directive_status`. No
compat shims — pre-prod, and stale tool names in Кая's context cost more than
they save.

**b) Report/Question → Кая.** Needs a **new Brain route** — the one piece
outside the tracker. `POST /event` on Brain (module-token authed): Brain resolves
the target agent's `delivery_addr` and pushes via the existing
`brain/delivery.py`. Module → Brain → agent, so the isolation rule holds and no
module ever learns an agent's address.

Then the Hub calls it on: terminal Report, new Question, project enrollment
request, and Directive `blocked`.

**Also:** `agents/kaya/CHANGELOG.md` entry — her tool set changes, which the
project rules require. Probably a `soul.md` line too, so she knows she can now
ask you a project's clarifying questions rather than just reporting status.

**Done when:** from Telegram you can send a Directive, watch it run, get asked a
clarifying question, answer it, and receive ✅ with a PR link — against the dummy
project from Step 7. This is the acceptance test for the whole design.

---

## Step 6 — Panel · M

`modules/tracker/panel.py` grows write actions. It's read-only today, which
means unsticking anything requires curl.

- cross-project fleet view (mirrored Status: who, what phase, blockers)
- queue with drag-to-reorder → `reprioritise`
- answer a pending Question from the browser
- approve a pending project
- cancel / requeue a Directive

**Done when:** you can drive a full Directive from the browser without touching
Telegram or curl.

---

## Step 7 — The dummy project · M

A **manual exercise harness**, not a product service and not a test suite:
`examples/dummy-project/` with its own compose file joined to the Kaizen network.
Its "pipeline" writes two Handoff files, pushes three Statuses, asks one
Question, and Reports `done` with a fake PR artifact. No real code, no `claude`
CLI, no git.

This is where the no-tests decision gets paid for: the harness is driven by hand
and watched in `make logs` and the panel. It's also the natural thing to convert
into the real acceptance suite later, when contracts settle.

Then walk the 16 cases in architecture §7 as a literal checklist. The ones that
matter most and are hard to test any other way:

- #1 offline at dispatch, #3 at-capacity
- #4 question answered, #5 question times out
- #7 cancel mid-flight
- #8 `docker kill` the Warden → lease expiry → requeue
- #9 restart the *Hub* mid-Directive → resync via `Health`
- #14 two concurrent Directives → distinct `task_id` trees, no file collisions

**Why before the VPN project:** every failure here is unambiguously a protocol
bug. Once a real fleet is running, a hang could be Alfred, a persona, a build, or
the protocol — and you'd be debugging four layers at once.

---

## Step 8 — The VPN project · L

Its own repo (`vpn-service` or similar), internally a monorepo:
`control-plane/`, `node-agent/`, `admin/`, `mobile/`, plus the agent layer:

- `warden.py` — `wardenkit` + config, thin
- `alfred.md` — the overseer persona
- `.claude/agents/*.md` — the fleet, adapted from
  `docs/reference/agent-pipeline/claude/agents/`. Start with ~6 (architect, one
  lead, two devs, one reviewer, security), not 19. Grow when a real gap appears.
- `.claude/commands/*.md` — pipelines; start with `/develop` and `/fix`
- `Dockerfile` — Go/Rust + `claude` CLI + `gh`
- `.claude/projects/*.md` — the rulebooks the personas reference. The export
  deliberately omits these; they're the most project-specific part and they're
  what makes the fleet's output any good.

**Carry over verbatim** from the export's safety rules: never touch `main`, never
`git revert`, never auto-resolve conflicts, auto-merge per-Directive only. With
`--dangerously-skip-permissions` and a `GH_TOKEN` in the container, these five
rules are the whole safety layer.

**Done when:** one real feature ships through the pipeline to a PR you'd merge.

---

## Step 9 — Docs and env · S

- rewrite `docs/tracker-poller.md` → the two integration tiers (full Warden vs
  ~30-line poller). It currently documents `app/tracker/` paths and a published
  port 8770 that compose doesn't publish.
- `.env.example`: Hub gRPC port, dispatcher/sweeper intervals, question ceiling
- `deploy/docker-compose.yml`: tracker's third port + healthcheck
- `README.md` architecture block: add the Warden hop

---

## Step 10 — Tests · deferred, not cancelled

Explicitly out of scope for now (see *Working constraints*), parked here so it
doesn't get lost. Pick it up once the proto and the schema stop moving —
realistically after Step 7, when the dummy harness has stopped finding protocol
bugs. At that point the harness from Step 7 is most of the work already done.

Highest-value targets, in order: the atomic claim under concurrency, the
Directive transition map, token/project isolation on every Hub RPC, and lease
expiry → requeue.

---

## Sequencing notes

**Order that matters:** 2 before 3 and 4 (both need the proto). 7 needs 3–5.
8 after 7. Step 0 is just housekeeping now, so it's only *first* by convenience.

**Order that doesn't:** 6 can slot in any time after 1. Step 9's docs can trail.

**Steps 1–4 are invisible to you as a user** — Кая's behaviour doesn't change
until Step 5. If you want something demonstrable sooner, we can pull the
`send_directive` / `cancel_directive` tools forward into Step 1 against the
existing dispatch-less flow, and let Кая drive the queue before any Warden
exists. Slightly more rework; much earlier feedback.

---

## Two decisions still open

Neither blocks Step 0. Both need answering before the step named.

1. **Fleet roster: per-project or shared?** (needed by Step 8) I assumed
   per-project, since personas cite repo-specific paths and rulebooks. The
   alternative — one roster in Kaizen injected into each project — saves
   duplication across many projects but makes every persona generic, which is
   exactly what the export warns costs output quality.

2. **May Alfred queue child Directives unattended?** (needed by Step 5) I
   assumed no: an epic decomposition comes back to you through Кая for
   confirmation before anything runs. Letting Alfred queue freely is more
   autonomous but means one vague directive can spawn a dozen runs.
