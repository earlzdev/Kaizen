# Dummy project — the tracker's exercise harness

<!--
WHAT: How to run the fake project that speaks the whole tracker v2 contract,
      and the checklist of behaviours to walk against it.
WHY:  docs/tracker-v2-plan.md Step 7 — every failure here is unambiguously a
      protocol bug. With a real fleet, a hang could be the overseer, a persona,
      a build, or the protocol, and you would be debugging four layers at once.
HOW:  bring it up, approve it, send it Directives with the keywords below.
-->

This is a **manual exercise harness**, not a product service and not a test
suite. It does no real work: no repo, no git, no `claude` CLI, no LLM. What it
does do is speak every part of the contract in
[`infra/proto/warden.proto`](../../infra/proto/warden.proto) — enroll, accept,
write Handoffs, push Status, ask the owner a question, report a PR.

It is also the smallest possible **worked example** of integrating a project:
[`warden.py`](warden.py) is the entire project side, and
[`Dockerfile`](Dockerfile) is the honest measure of what it costs (two Python
packages).

## Run it

```bash
# 1. The Kaizen stack must be up first — this joins its network.
make up

# 2. Build and start the dummy project (its own compose stack, on purpose).
docker compose -f modules/tracker/example/dummy-project/docker-compose.yml up -d --build

# 3. It is now PENDING. Approve it — any of these work:
#    · ask Кая: "what projects are asking to join the tracker?" → "approve dummy"
#    · the panel: http://localhost:8770/panel → Fleet → Approve
#    · curl:
curl -X POST -H "Authorization: Bearer $TRACKER_ADMIN_TOKEN" \
     http://localhost:8770/projects/dummy/approve

# 4. Watch both sides.
docker compose -f modules/tracker/example/dummy-project/docker-compose.yml logs -f
make logs
```

## Driving it

Send Directives the way you actually will — through Кая ("ask dummy to do
X"), from the panel, or with curl. The dummy reads **keywords in the intent** to
decide how to behave, so every case below is reachable without editing this
file:

| keyword | what it does |
|---|---|
| *(none)* | the full pipeline: 3 agents, 2 Handoffs, 1 question, a PR → `review` |
| `hang` | never finishes — for cancel and lease-expiry walks |
| `fail` | reports `failed` with a fake build-log tail, and no PR |
| `block` | reports `blocked` (out-of-scope work) and stops |
| `noask` | skips the clarifying question |
| `quick` | goes straight to the report |

Two kinds are special, and neither needs a keyword:

- `kind=epic` comes back `done` with three child Directives, which the Hub
  queues under it.
- `kind=review` is the follow-up the Hub queues when you grant auto-merge: it
  "merges" and reports **`done`** with the PR. Every other path stops at
  `review`, because a PR nobody has merged is waiting on you.

## The checklist (docs/tracker-architecture.md §7)

Walk these in order. The ones marked 🖐 need you to do something to a container.

| # | Case | How to walk it | What should happen |
|---|---|---|---|
| 1 | Project offline at dispatch | 🖐 `docker stop dummy-warden`, then send a Directive | Stays `queued`, `dispatch_attempts` climbs with widening gaps. Кая tells you only after `TRACKER_DISPATCH_MAX_ATTEMPTS` |
| 2 | Unsupported kind | send `kind=research` (not in its manifest) | → `failed` immediately, reason surfaced. Кая should refuse it even earlier — `send_directive` checks the manifest first |
| 3 | At capacity | send 3 at once (`MAX_CONCURRENT=2`) | The third stays `queued`, silently — no message |
| 4 | Agent needs clarification | send anything without `noask` | → `blocked`; the question reaches you; answer it → `running` → `review` |
| 5 | Question times out | same, then ignore it for `ASK_TIMEOUT` | The agent proceeds **under a stated assumption** and says so. Never a silent guess |
| 6 | Out-of-scope work | send `block noask` | → `blocked` with the reason, and it stays there — the decision is yours |
| 7 | Cancel mid-flight | send `hang noask`, then cancel it | Pipeline killed, `docs/tracker/` left intact, → `cancelled` |
| 8 | Warden crashes | 🖐 send `hang noask`, then `docker kill dummy-warden` | Heartbeats stop → lease expires → requeued → re-dispatched when it returns |
| 9 | Hub restarts mid-flight | 🖐 send `hang noask`, then `docker compose restart tracker` | On boot the Hub polls `Health`: work the project IS running keeps its lease; work it is not is requeued |
| 10 | Review finds must-fix issues | *(internal to a real project)* | The Hub sees only `PushStatus` phase changes — it deliberately does not model pipeline internals |
| 11 | Build fails | send `fail noask` | → `failed` with the log tail as `error`, and **no PR** |
| 12 | PR open, awaiting you | send anything (it ends in `review`), then `grant_auto_merge(id)` via Кая | A `review` Directive is queued under it, sharing its `task_id`; it merges and reports `done` with the PR |
| 13 | Epic | send `kind=epic` | Epic → `done`, three children queued under it at ascending priority |
| 14 | Two at once | send two similar intents together | Distinct `task_id`s → distinct `docs/tracker/{task_id}/` trees, no file collisions |
| 15 | "What's everyone doing?" | ask Кая, or open the panel's Fleet view | Every project's live agents in one answer |
| 16 | Token rotation | 🖐 `POST /projects/dummy/rotate` | Warden gets `UNAUTHENTICATED` → re-enrolls → you approve once → back to work |

## Looking at what it produced

The Handoffs and Status files are real files in the project's volume — that is
the point of them being files:

```bash
docker exec dummy-warden find /repo/docs/tracker -type f
docker exec dummy-warden sh -c 'cat /repo/docs/tracker/*/tasks/*.md'
docker exec dummy-warden sh -c 'cat /repo/docs/tracker/*/status/*.yml'
```

## Tearing it down

```bash
docker compose -f modules/tracker/example/dummy-project/docker-compose.yml down -v
# and drop its rows, if you want a clean slate:
#   make psql  → \c tracker  → DELETE FROM tracker_projects WHERE name='dummy';
```
