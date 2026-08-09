# Connecting a project to the tracker

<!--
WHAT: How another project joins the Kaizen tracker — the two tiers it can join
      at, what each costs, and the exact contract for both.
WHY:  replaces docs/tracker-poller.md, which documented `app/tracker/` paths and
      a published port 8770 that compose does not publish. It also only knew
      about one tier, because v1 only had one.
HOW to read it: pick your tier from §1, then read only that section.
-->

There are **two ways** for one of your projects to take work from the tracker,
and the Hub does not care which you pick.

| | **Warden** (full) | **Poller** (simple) |
|---|---|---|
| What it is | A small gRPC daemon in your project's container | ~30 lines of HTTP polling |
| Gets work by | The Hub dials it the moment work exists | Asking every N seconds |
| Can report progress | Yes — per-agent status, live in the panel | Only the final result |
| Can ask you questions | Yes — blocks until you answer | No |
| Survives crashing | Yes — the lease expires and work is requeued | No — the work sits claimed |
| Costs | `pip install grpcio protobuf` + one file | one file, no dependencies |
| Use it when | The project has an agent fleet doing real work | A script needs a queue |

Vocabulary (Directive, Handoff, Status, Report, Question) is defined in
`docs/tracker-architecture.md` §1.

---

## 1. The Warden tier

A working example lives in [`modules/tracker/example/dummy-project/`](../modules/tracker/example/dummy-project/) —
read `warden.py` and its `Dockerfile`; together they are the entire project side.

### What you write

```python
from infra.wardenkit import (
    DirectiveJob, HubClient, JobResult, WardenServicer, make_manifest, serve,
)

async def run_pipeline(job: DirectiveJob) -> JobResult:
    """Your project's actual work. Everything else is the kit's."""
    await job.status("architect", "in_progress", phase="design",
                     progress="working out what to build")

    # Ask the owner when a decision is genuinely theirs. This BLOCKS until they
    # answer or `timeout_sec` elapses — check `.answered`, because an empty
    # answer is still an answer.
    answer = await job.ask("architect", "Break backwards compatibility?",
                           timeout_sec=900, suggested=["yes", "no"])
    if not answer.answered:
        ...  # proceed under a STATED assumption, or return JobResult("failed")

    # Handoffs are files in your repo — greppable, and they survive a restart.
    job.handoff("architect", "developer", "## Spec\n...")

    # ALWAYS end through finish(): it writes the Status from the JobResult, so
    # the two cannot disagree. See "The one rule" below.
    return await job.finish("architect",
                            JobResult(state="review", summary="PR is open",
                                      artifacts=[{"type": "pr", "url": pr_url}]),
                            phase="design", progress="PR open")

async def main():
    hub = HubClient("tracker:9104", token_path="/state/hub_token")
    manifest = make_manifest(
        "my-project",
        purpose="one line: what this project is for",
        kinds=["develop", "fix"],       # what you accept; others are rejected
        max_concurrent=2,
        grpc_addr="my-project:9200",    # what the Hub dials to reach you
        roster=[                        # your fleet — see "The roster" below
            {"slug": "product-owner-ohno", "name": "Taiichi Ohno",
             "role": "Product Owner", "tier": "product", "model": "opus"},
            {"slug": "architect-xavier", "name": "Charles Xavier",
             "role": "Solution Architect", "tier": "architect", "model": "opus",
             "reports_to": "product-owner-ohno"},
            {"slug": "backend-lead-tesla", "name": "Nikola Tesla",
             "role": "Backend Team Lead", "tier": "lead", "area": "backend",
             "reports_to": "architect-xavier"},
        ],
    )
    server = await serve(WardenServicer(manifest, run_pipeline, hub=hub,
                                        repo_root="/repo"))
    await hub.enroll(manifest)          # waits for the owner's approval
    await server.wait_for_termination()
```

### The one rule: `job.finish()` on every terminal path

A handler must never write a Status next to a `return`. The first real project
did this:

```python
_note(job, "done", f"{command} exited {code}")   # always
if code != 0:
    return JobResult(state="failed", ...)        # afterwards
```

`done` there meant "the Warden finished dispatching", not "the task succeeded".
The status relay mirrors that file to the Hub, so the panel, Telegram and every
reader of agent status saw success while the Directive's own state said failure
— one record holding both halves. It cost a milestone that looked integrated and
was not.

`job.finish(agent, result, progress=...)` writes the Status **from** the result
and returns the result. There is no shape of it that records one thing and
reports another. The servicer additionally logs an error when the Status files
on disk contradict the Report — that catches the other source of this bug, a
Claude sub-agent writing `status/*.yml` with a file-write tool, which goes
through no Python at all.

### Running the `claude` CLI: use `ClaudeRunner`

Do not hand-roll the subprocess call. `infra.wardenkit.ClaudeRunner` exists
because the first hand-rolled copy shipped six defects at once:

```python
runner = ClaudeRunner(cwd="/repo", model="sonnet", effort="low")
run = await runner.run(prompt, agent="product-owner-ohno")   # persona, not a command

if run.quota_exhausted:            # terminal — NEVER report this as `blocked`
    return await job.finish(slug, JobResult(state="failed",
                                            summary=run.failure_summary()))
if not run.ok:
    ...                             # run.tail says why; run.failure_summary() is one line
return await job.finish(slug, JobResult(state="done", summary=run.answer))
```

What it fixes, and why each one bit:

| | |
|---|---|
| merged stderr | everything the CLI says before it can emit JSON — no credential, bad flag, missing binary — used to go to an unread pipe, which also deadlocks the child |
| `run.answer` on success | the model's closing text is the deliverable for any prose kind; capturing it only on failure made a finished research run report a list of paths |
| leading-`-` guard | a prompt starting with `-` is parsed as an option: `error: unknown option '--- what the owner said…'`. Use `===` in prompt templates |
| quota detection | a spent subscription exits non-zero exactly like a crash |
| first-output timeout (120s) | ten minutes is right for a pipeline mid-thought and wrong for one that never started — and it holds the only slot on `MAX_CONCURRENT: 1` |
| stripped `GH_TOKEN` / `ANTHROPIC_API_KEY` | `printenv` in any agent's Bash, and API billing on a Max subscription |

**Never report a spent quota as `blocked`.** `blocked` is a *leased* state
meaning "an agent asked the owner a question and is holding the job open", and
the kit heartbeats through it deliberately. A Warden that returns it and stops
heartbeating lets the lease expire (120s), the sweeper requeues (every 30s), the
quota is still spent — and the Directive loops every two and a half minutes for
the whole outage, notifying the owner on every turn.

### The roster

Your roster is what the panel's **Team** tab draws as an org chart, so the three
structural keys (`tier`, `area`, `reports_to`) are worth the two extra lines.
Each entry is a plain dict:

| key | what it is |
|---|---|
| `slug` | the identifier, **and the join key**: send exactly this as `agent_slug` on every Status, or the live state lands on nobody |
| `name` | what a human is called on the chart ("Nikola Tesla"). Renaming this never breaks the Status join — that is why it is separate from `slug` |
| `role` | free text, the label the owner reads ("Backend Team Lead") |
| `model` | informational only; the Hub never picks models |
| `tier` | **the standard vocabulary**: `owner` \| `product` \| `architect` \| `lead` \| `developer` \| `reviewer`. This is the *shape*, where `role` is the *label*. `owner` is the **human** the work is for — the Hub seeds it, do not claim it; `product` is a **Product Owner agent**, which sits above the architect |
| `area` | which part of the project this persona works on — `backend`, `frontend`, `android`, … Free text; the Hub groups by whatever you use. One column per area |
| `reports_to` | the `slug` of the persona above it. Omit on whoever sits at the top |

Only `slug` (or `name`) is required. Everything else is optional, and everything
here is **display only** — the Hub dispatches to a PROJECT, never to a persona.

`tier` is a fixed five-word vocabulary rather than free text because every
project runs a different fleet with differently-named personas, and the Hub has
to be able to draw a hierarchy for all of them; `area` stays free text because
no two projects split their work the same way. What you declare is
stored as-is and always wins. What you leave blank the Hub guesses from the slug
and role — and **stores what it guessed**, so a wrong guess is visible in
`GET /agents` instead of being silently re-invented on every render. `reviewer`
personas are drawn in a cross-cutting band under the columns rather than in one
team's column. Owners, product owners and architects span every area, so any
`area` on them is dropped.

**If your fleet has a Product Owner, declare `tier: "product"` for it and point
the architect's `reports_to` at it.** The chart then reads owner → PO →
architect → leads → developers, which is the actual chain of command: the PO
decides *what* is built, the architect decides *how*. Do not give it
`tier: "owner"` — that tier means the human, it would be labelled human on the
chart, and the Hub's owner-seeding check would then find a row and never add
you. The guess also finds it on its own from the words "product" + "owner"
together, so a persona slugged `product-owner-*` lands correctly even
undeclared; `product-designer` and `product-analyst` deliberately do not.

A full 13-persona example is in the dummy project's `warden.py`.

### What the kit does for you

Enrollment and the token (stored at `token_path`, re-issued if it is ever
rotated); heartbeats for as long as a job lives, so a crash is noticed and the
work requeued; the at-capacity and unsupported-kind answers; accepting a
re-dispatch of something already running without starting it twice;
cancellation; **`Restart`** (below); the `docs/tracker/{task_id}/{tasks,status}/`
file layout — and the **status relay** below.

### Restart — for a fleet that is wedged rather than dead

A fleet can end up alive but not moving: the process is up, so nothing expires,
but no agent is making progress. (A laptop that slept through a run is the
common way in.) `Restart` is how the owner clears that without walking to the
host:

- **the project restarts itself; the Hub only relays.** The Hub dials projects
  across a network and holds no Docker socket and no ssh key — that would be
  root-equivalent power over every machine that ever registered.
- **`scope: "jobs"`** (default) drops in-flight work, stays up, goes idle. The
  process and its logs survive for inspection, and the Hub requeues what was
  dropped, so nothing is lost — every artifact is still a file in your repo.
- **`scope: "self"`** additionally exits with code 42 after acking, leaving your
  container's restart policy to start a fresh process. **Set
  `restart: unless-stopped`** or this does nothing.
- **A second request inside 60s is refused**, with a reason. A restart storm
  against a project that is merely slow costs more than the wedge it clears.
- Override the behaviour with `restart_hook=` on `WardenServicer` if your
  project knows how to re-exec its own pipeline runner.

**An unreachable Warden cannot restart itself.** If the process is gone, this RPC
answers "unreachable — this needs a restart on the host"; that case belongs to
your container's restart policy, not to the Hub.

### The status relay — why your agents show up as busy

`job.status()` writes the file and pushes the row together, which is right for a
pipeline written in Python. But most fleets are Claude sub-agents: they write
`docs/tracker/{task_id}/status/{slug}.yml` with a file-write tool and cannot call
back into the Warden process. Left alone, every one of them sits at **idle** in
the panel no matter how much work it is doing.

So while a job runs, the Warden **polls that directory every few seconds and
pushes whatever changed** (`status_poll_seconds`, default 5; a final sweep runs
after the job ends so the last transitions are not lost). Only changes are
pushed — re-pushing an unchanged row would refresh `updated_at` and make a
stalled agent look freshly active.

Two rules your personas must follow for this to work, because both failures are
silent:

- **The filename is the join key.** `status/dev-anderson.yml` mirrors onto the
  roster member whose `slug` is `dev-anderson`. A file named after a short handle
  (`anderson.yml`) lands on nobody, and the real member stays idle.
- **The state field is `state`.** `status:` and `updated:` are accepted as
  aliases (a hand-written file will use whatever the persona's example showed),
  but `state` + `updated_at` are canonical.

Values the panel understands: `idle`, `pending`, `in_progress`, `blocked`,
`review`, `done`.

### Enrolling

1. Bring your project up. It registers and waits — nothing is issued yet.
2. Approve it: ask Кая ("which projects are requesting access?" → "approve X"), or the panel's
   **Fleet** view, or
   `POST /projects/{name}/approve` with the admin token.
3. Its next check-in claims a token, and it can be given work.

The Warden generates an enrollment secret on first boot and keeps it beside its
token. That is what proves the project claiming your approval is the same one
that asked for it — so approve deliberately, and if a project's container is
ever compromised, rotate with `POST /projects/{name}/rotate` and
`{"reset_secret": true}`.

### Networking

Your project joins the Kaizen compose network and reaches the Hub at
`tracker:9104`; the Hub dials you back at whatever `grpc_addr` your manifest
declares. Nothing is published to the host. See the dummy project's
`docker-compose.yml` for the `external: true` network stanza.

---

## 2. The poller tier

For a project that just wants a queue. No gRPC, no dependencies beyond an HTTP
client. The owner registers it and hands over the token:

```bash
curl -X POST -H "Authorization: Bearer $TRACKER_ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name":"scripts","purpose":"my odds and ends"}' \
     http://tracker:8770/projects
# -> {"project": {...}, "token": "..."}   ← shown exactly once
```

Then, in your project:

```python
import time, httpx

BASE, TOKEN, PROJECT = "http://tracker:8770", "<the token>", "scripts"
h = {"Authorization": f"Bearer {TOKEN}"}

while True:
    r = httpx.post(f"{BASE}/projects/{PROJECT}/claim", headers=h,
                   json={"agent": "worker-1"})
    if r.status_code == 204:          # nothing queued
        time.sleep(30); continue
    task = r.json()["task"]

    httpx.post(f"{BASE}/tasks/{task['id']}/report", headers=h,
               json={"status": "running"})
    try:
        summary = do_the_work(task["title"], task["description"])
        httpx.post(f"{BASE}/tasks/{task['id']}/report", headers=h,
                   json={"status": "done", "summary": summary,
                         "artifacts": [{"type": "pr", "url": "..."}]})
    except Exception as e:
        httpx.post(f"{BASE}/tasks/{task['id']}/report", headers=h,
                   json={"status": "failed", "error": str(e)})
```

### The states you may report

`queued → dispatched → running → blocked|review → done|failed|cancelled`

A claim moves the Directive to `dispatched` (v1 called this `claimed`; that
state is gone). The Hub **enforces** these transitions: an illegal jump —
reporting `done` on something never claimed, or moving a finished Directive
back — answers **409** with an explanation. Re-reporting the state it is
already in is a harmless no-op, so a retry after a network blip is safe.

### What the poller tier gives up

No live progress, no questions, and — importantly — **no lease**. A poller that
dies leaves its Directive sitting in `dispatched` until you requeue it from the
panel. That is deliberate: pollers do not heartbeat, so the Hub cannot tell a
dead one from a slow one, and requeueing on a timer would kill legitimate
long-running work.

---

## 3. Both tiers: the routes

| Route | Auth | What |
|---|---|---|
| `GET /health` | none | liveness |
| `POST /projects` | admin | register a project → `{project, token}` |
| `GET /projects` | admin | list them (never includes tokens) |
| `POST /projects/{p}/approve` | admin | approve an enrolling project |
| `POST /projects/{p}/rotate` | admin | invalidate its token |
| `POST /tasks` | admin | queue a Directive |
| `GET /tasks?project=&status=` | admin | observe |
| `GET /tasks/{id}` | admin or owning project | read one |
| `POST /tasks/{id}/cancel` | admin | abort it, telling the project |
| `POST /tasks/{id}/requeue` | admin | unstick it |
| `POST /projects/{p}/reprioritise` | admin | reorder the queue |
| `GET /activity` | admin | live per-agent status, all projects |
| `GET /questions` · `POST /questions/{id}/answer` | admin | the owner's half of a blocked agent |
| `POST /projects/{p}/claim` | project | atomically claim the next queued (or 204) |
| `POST /tasks/{id}/report` | project | `{status, summary?, artifacts?, error?}` |
| `GET /` · `GET /panel` | none (token typed in) | the web console |

A project token can only ever touch its OWN project's Directives; anything else
answers 404, identically to a Directive that does not exist.
