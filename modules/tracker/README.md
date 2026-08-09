# Tracker — running your projects through Kaya

Tracker is Kaizen's project-management module: a Hub that queues work,
dispatches it to your other repos, and reports back through Kaya. This
README is the owner's-eye view — how you actually use it day to day. For
the design and the wire contracts, see:

- [`docs/tracker-architecture.md`](../../docs/tracker-architecture.md) —
  vocabulary (Directive, Handoff, Status, Report, Question), the Hub↔Warden
  scheme, the full tool surface.
- [`docs/tracker-integration.md`](../../docs/tracker-integration.md) — how
  to connect one of your own projects (the Warden vs. Poller tiers), with a
  working example at
  [`modules/tracker/example/dummy-project/`](example/dummy-project/).
- [`docs/tracker-v2-plan.md`](../../docs/tracker-v2-plan.md) — the design
  history, if you want the "why" behind a decision.

## The idea

Each of your projects that wants work from Kaizen runs a small daemon (a
**Warden**) that Kaizen's Hub can dial directly. You tell Kaya what you
want done, in your own words; Kaya turns that into a **Directive** and
sends it to the right project. The project's own agent fleet — usually one
overseer persona, more if the project has grown one — picks it up, does the
work, and reports back: progress along the way, a final PR or answer at the
end, or a question if it's stuck on a decision only you can make.

You never structure the request yourself. `send_directive` takes your
intent verbatim; interpreting it into an actual plan is the receiving
project's job.

## Talking to it through Kaya

These are the tools Kaya has (`modules/tracker/tools.py`), reached over MCP
through Brain — there's nothing else to install or run separately.

**Sending and tracking work**
- `send_directive(project, intent, kind?, priority?)` — queue work. `kind`
  is one of `develop`, `fix`, `refactor`, `research`, `review`, `epic`,
  `brainstorm`, `analyze`, `ask`, `converse`, `deploy` — a project only
  accepts the kinds its own manifest declares, so an unsupported kind is
  rejected immediately instead of failing minutes later.
- `directive_status(id)` — state, current phase, per-agent status, and any
  blockers.
- `list_directives(project?, state?)` — filter the queue/history.
- `cancel_directive(id, reason)` — pull work back, whatever state it's in.
- `reprioritise(project, ordered_ids)` — reorder a project's queue.
- `project_activity(project?)` — "who is doing what right now", across one
  project or all of them at once.

**Questions and decisions**
- `pending_questions()` — things a project's agent is blocked on, waiting
  for you.
- `answer_question(id, answer)` — unblocks it.
- `grant_auto_merge(id)` — let a specific Directive's PR merge itself once
  it's green, instead of waiting on you.

**Projects**
- `list_projects()` / `describe_project(project)` — what's registered, its
  purpose, supported kinds, and live fleet.
- `pending_projects()` / `approve_project(name)` — a new project shows up
  here once its Warden enrolls; nothing runs until you approve it.

## Directive states

`queued → dispatched → running → { blocked | review } → done | failed | cancelled`

- **blocked** — the project asked you a Question; it's waiting, not stuck.
- **review** — a PR is open; the ball is in your court, not the project's.
- Everything else reports itself; you don't need to poll.

## Dashboards

- The tracker's own panel at `:8770` (desktop, left open on a monitor).
- A read-only mobile view at Brain's `GET /admin/tracker` — same data,
  phone-first layout, only ever needs Brain's own admin token.

## Connecting a new project

Short version: your project runs a small gRPC daemon (a Warden) built on
`infra/wardenkit`, declares what kinds of work it accepts and who's on its
fleet, and registers with the Hub. Read
[`docs/tracker-integration.md`](../../docs/tracker-integration.md) for the
full contract and a working example — it's a page, not a project.
