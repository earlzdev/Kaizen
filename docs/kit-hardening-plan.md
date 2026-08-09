# Kit hardening — folding the pilot project's findings back into Kaizen

<!--
WHAT: The plan for turning two days of the pilot project's discoveries into kit-level fixes,
      so project number two does not repeat them.
WHY:  the pilot project was the first project driven end-to-end by tracker v2. Almost none of
      the 17 defects it surfaced are the pilot project's own — they live in
      `infra/wardenkit`, `infra/agentkit`, the `new-project` skill, or the Hub.
HOW to read it: §1 is the audit (what is already fixed, what is not). §2 is the
      ordered work. §3 is the two decisions the owner has to make.
-->

Source: the pilot project's post-mortem, 17 items. This document is the Kaizen-side
translation — **the defect and the reasoning, never the pilot project's files verbatim.**

---

## 1. Audit — where each item actually stands today

| # | Item | Status | Where it lives now |
|---|---|---|---|
| 1 | status says `done`, result says `failed` | ✓ | `DirectiveJob.finish()` + `_warn_if_status_disagrees()` in `infra/wardenkit/servicer.py` |
| 2 | stderr discarded | ✓ | `stderr=STDOUT` in `infra/wardenkit/clirunner.py` |
| 3 | result text discarded on success | ✓ | `CliRun.answer`, populated on success too; per-kind `ARTIFACT_GLOBS` in the warden template |
| 4 | real `.env` inside the RW mount | ✓ | `.env.example:/repo/.env:ro` in `templates/deploy/warden/docker-compose.yml` |
| 5 | `GH_TOKEN` readable by any agent | ✓ | `env -u GH_TOKEN gh auth login` + `exec env -u` in `templates/deploy/warden/entrypoint.sh`; stripped again in `clirunner._env()` |
| 6 | Warden update needs a rebuild | ✓ | runs from the mount; the image copy stays as the documented fallback |
| 7 | container is root, owns the owner's repo | ✓ | `user: "${HOST_UID}:${HOST_GID}"`, `HOME=/state`, one-time chown printed by the renderer |
| 8 | one working tree ⇒ `MAX_CONCURRENT: 1` | ✗ **deferred by design** — Step 7 | — |
| 9 | quota exhaustion looks like a crash | ✓ | `infra/quota.py`, shared by `clirunner` and `agents/core/cli.py`; reported terminally, never `blocked`; two timeouts |
| 10 | every persona on opus, default effort | ✓ | `MODELS` in `render.py`, `{{REASONING_EFFORT}}` in `MANIFEST.md` §3, `REASONING_EFFORT`/`CODE_REASONING_EFFORT` in the warden template |
| 11 | no cheap way to just ask a question | ✓ | Hub side was already done; project side is `run_ask()` — `--agent` persona, tree diff, `review` when it edited |
| 12 | a persona has no memory between runs | ✓ | durable: `templates/docs/decisions.md`; short-term: `infra/wardenkit/conversation.py`, in the state volume |
| 13 | research commissioned for non-research | ✓ | three-part threshold + worked examples in `agents/product-owner-ohno.md` |
| 14 | prompt beginning with `-` kills the run | ✓ | guard in `ClaudeRunner._build()`; templates use `===` |
| 15 | notifications fail silently when unconfigured | ✓ | tracker refuses to boot, Brain banners (`infra/config_checks.py`), plus `make notify-selftest` |
| 16 | two assistants on one bot token | ✓ | pre-flight probe + conflict diagnosis in `agents/kaya/main.py` |
| 17 | setup / Makefile papercuts | ✓ | `templates/Makefile` (`--env-file` on the variable, prod refuses placeholders, guarded `vendor-kit`) + the two traps in `docs/agents/secrets-and-env.md` |

**Legend:** ✓ done · ◐ partially done · ✗ not started.

**Still open: item 8 only.** Everything else is done, including the `SKILL.md`
rewrite around `render.py` (563 → 406 lines) and the doc sync across
`tracker-integration.md`, `secrets-and-env.md`, `new-project.md` and
`MANIFEST.md` §5 (invariants 13–15).

**Nothing here has been run in docker.** The template `Dockerfile` has never been
built and no rendered project has been booted. Verification so far is: modules
compile, the renderer produces a verified idempotent tree, and the quota /
dash-guard / conversation-window logic was exercised directly.

---

## 2. The work, in order

Each step is independently shippable. Per the standing rule for tracker v2, the
gate after every step is **`/agentic-loop`, not a test suite** — no automated
tests while the contracts are still moving.

---

### Step 0 — `infra/wardenkit/clirunner.py`: the missing half of the kit

**Why first.** Items 2, 3, 9, 12 and 14 are all the same defect: the kit hands a
project a transport and lets it write the hard part itself. Six bugs per
project, rediscovered each time. This step makes them unwritable.

New module, exported from `infra/wardenkit/__init__.py`:

```python
@dataclass
class CliRun:
    code: int                 # the CLI's exit code
    answer: str               # the final {"type":"result","result":…} text
    tail: str                 # last N KB of the merged stream, for diagnosis
    quota_reset: str | None   # parsed reset time when the subscription is spent
    started: bool             # did the CLI emit anything at all
```

What it must do, and why each one is a pilot-project finding:

- **`stderr=asyncio.subprocess.STDOUT`** (item 2). The drain collects, it does
  not parse, so merging is safe — and it removes a real deadlock: a pipe nobody
  reads fills and wedges the child. *(Note: `agents/core/cli.py:213` avoids the
  deadlock differently, by draining stderr in a concurrent task. Either is
  correct; the runner picks merging because it also puts the cause in the tail.)*
- **Return the `result` event text on success too** (item 3), not only in the
  `code != 0` branch. For any kind whose deliverable is prose, that text *is*
  the deliverable.
- **Leading-dash guard** (item 14), once, where the command is built:
  `if prompt.startswith("-"): prompt = "\n" + prompt`.
- **Quota detection** (item 9): recognise the spent-subscription message, parse
  the reset time, and surface it as a distinct field — so the caller can report
  it *terminally* and never as `blocked`.
- **Two timeouts, not one** (item 9): a short **first-output** timeout (120s)
  plus the existing idle timeout (10 min). Ten minutes is the right patience for
  a pipeline mid-thought and the wrong patience for one that never began.
- **Strip `GH_TOKEN`** from the child's environment (item 5, half two), beside
  the existing `ANTHROPIC_API_KEY` strip.
- **A bounded conversation window** (item 12, short-term half): last N
  exchanges, answers clipped, persisted under the **state volume** — never the
  repo. Conversation is not a project artifact, and committing every question
  the owner typed puts it in the PR history. Explicitly **not** `--resume`: a
  resumed session carries unbounded history into every request, on the resource
  that is already the binding constraint.

**Do not let the runner parse the pipeline's output.** Its job is translation —
prompt in, command out, result back. Every place it starts interpreting what the
fleet meant is a second, worse copy of the fleet.

**Then rewire the consumers:** `docs/tracker-integration.md` §1 and skill §8b
must show `run_pipeline` built on the runner, and the dummy project should keep
its no-LLM handler but reference the runner as what a real project uses.

---

### Step 1 — the outcome invariant (item 1)

The worst item: a record that contradicts itself gets read as success, because
success is the half people act on. It cost a milestone that looked integrated
and was not.

In `infra/wardenkit/servicer.py`, add to `DirectiveJob`:

```python
async def finish(self, agent_slug: str, result: JobResult, *, progress: str = "", **kw) -> JobResult:
    """Write the agent's status FROM the result, then return the result.
    There is no shape of this call that records one thing and reports another."""
    await self.status(agent_slug, result.state, progress=progress, **kw)
    return result
```

Every terminal path in a handler becomes `return await job.finish(agent, JobResult(...), progress="…")`.

Then make the invariant enforceable rather than advisory:

- document it in `docs/tracker-integration.md` §1 as the terminal-path rule;
- add it to `MANIFEST.md` §5 as an invariant the scaffolder must not break;
- **log loudly in `_run`** when the last relayed status for a job disagrees with
  the reported terminal state — the relay already tracks per-agent fingerprints
  in `self._relayed`, so the check is nearly free and it catches a project that
  hand-rolls its own statuses anyway.

---

### Step 2 — credentials (items 4, 5)

Live in every project scaffolded today.

**4 — the owner's real `.env` inside the fleet's RW mount.** A rule an agent is
asked to follow is a request; a mount is a fact. The kit default becomes:

```yaml
volumes:
  - ../..:/repo
  - ../../.env.example:/repo/.env:ro
```

Inside the container `/repo/.env` holds the committed fake values. Nothing
breaks — the Warden gets its variables through `environment:`, resolved from the
real `.env` on the host by `--env-file`. Lands in: skill §8b (compose recipe),
§10 (secrets), the failure-modes list, and `docs/agents/secrets-and-env.md`.

**5 — `GH_TOKEN` readable by any agent with Bash.** Two halves; neither works
alone:

1. Entrypoint stores the token in `gh`'s own config:
   `printf '%s' "$GH_TOKEN" | env -u GH_TOKEN gh auth login --with-token`.
   `env -u` is required, not cosmetic. **Must not be fatal** — `git push` still
   works from the credential store, and a hard exit under
   `restart: unless-stopped` reads as a broken image.
2. Strip `GH_TOKEN` from the CLI's environment (done in Step 0).

**This reverses skill §10's current instruction**, which forbids
`gh auth login --with-token` outright. That paragraph was right about the
symptom and wrong about the cure — rewrite it, do not delete it: the restart-loop
warning is what stops the next person from dropping `env -u`.

Verification (a live run, never by reading the value): the boot line changes from
`Logged in … (GH_TOKEN)` to `Logged in … (/root/.config/gh/hosts.yml)`, and
`gh pr list` still returns data.

**State the residue honestly in the kit's docs:** `~/.git-credentials` remains
plaintext and `cat` still reads it. Closing that needs a credential helper that
does not store plaintext.

---

### Step 3 — the subscription is the real constraint (items 9, 10)

**9 — quota, on top of Step 0's detection.** The reporting shape matters more
than the detection:

- report it **terminally**, with the reset time from the CLI's own message:
  `⏳ subscription exhausted — resets at 19:00. It won't retry on its own, resend after the reset.`
- **never as `blocked`.** This looks right and is a trap: `blocked` is a *leased*
  state (see `servicer.py`'s header — the kit heartbeats through it deliberately).
  A Warden that returns and stops heartbeating leaves the lease to expire (120s),
  the sweeper requeues (30s), the quota is still spent, and the Directive loops
  every two and a half minutes for the whole outage, notifying every turn.
- mirror the same detection into `agents/core/cli.py`, which today recognises
  only "not logged in" — Кая hits the same wall on the Max backend.

**10 — model and effort defaults**, in `infra/agentkit/MANIFEST.md` §3 and the
personas:

- reviewer, security and researcher → the smaller model (today §3 puts security
  on opus and says nothing about researcher);
- architect and developers stay on the larger one;
- the `ask` conversation persona (Step 4) runs on the smaller model;
- **new slot `{{REASONING_EFFORT}}`**, defaulting low, **with the caveat written
  where it is set**: low is right while the fleet writes plans and decisions, and
  wrong once it produces code — effort not spent writing is effort a reviewer
  pays back with interest.

---

### Step 4 — from batch system to something the owner talks to (items 11, 12, 13)

**11 — project side of `ask`.** The Hub half is done; the project half is not.
The kit must show, in skill §8b and `docs/tracker-integration.md`:

- map `ask` to a **persona**, not a slash command — every slash command runs a
  pipeline, creates directories and reports paths;
- return the model's answer as the summary (Step 0 makes that possible);
- **diff the tree before and after** and report anything that changed, rather
  than assuming nothing did.

Adopt the pilot project's shape, which beat the alternative: let the consulted persona make
small edits under `docs/` and return `review` instead of `done` when it did.
*Allowed to act, obliged to disclose.* A read-only mode could not even run
`gh pr list`, and answered questions about live state by reconstructing it from
the tree — it reported one PR open and another absent when the truth was the
reverse.

**12 — memory, two layers, kept separate.**

- **Durable:** a decision journal in the repo the persona reads first and appends
  to last. One line per decision: date, what, why. The test for whether a line
  belongs: *if the next run does not know this, will it decide wrongly?* Survives
  rebuilds, quota resets and container wipes; the owner can read and correct it.
  Lands as a new `infra/agentkit/rulebooks/` entry plus a pre-read line in the
  personas that need it.
- **Short-term:** the bounded window from Step 0, in the state volume.

**13 — a test for commissioning research, not a preference.** Commission it only
when **all three** hold: the answer needs evidence from **outside the
repository**; the decision it feeds is **expensive to reverse**; and the persona
genuinely **cannot get a usable answer itself** in a handful of searches. Any one
false → answer it and name the uncertainty. An answer with a stated uncertainty
beats a research run the owner has to wait for.

Include worked examples in both columns — they carry the threshold better than
the rule does. *Real:* which protocol survives filtering, which admin panels the
industry uses, whether two daemons can share a host. *Not:* how to rebase, what
the project status is, what is left on a task. Lands in
`product-owner-ohno.md` (§ next to the existing "technical research is not
yours" line) and `commands/research.md`.

---

### Step 5 — the operational papercuts (items 6, 15, 16, 17)

**6 — Warden from the mount.** `command: ["python", "/repo/deploy/warden/warden.py"]`
+ `PYTHONPATH` pointing at the vendored kit in the mount. Updates become
`git pull && docker restart`. **State the cost in the kit:** the container is no
longer self-contained — start it mid-checkout and it runs a half-written file.
Acceptable when the repo and the container share a machine. Keep the image's own
copy as the documented fallback (`command: ["python", "/app/warden.py"]`). This
does **not** remove the mid-run interruption, only the build.

**15 — notifications.** Swallowing delivery failures at *runtime* is right and
stays — a notification outage must not fail a report an hour of work produced.
But *never configured* is a deployment mistake, not a runtime failure, and today
it is a single `logger.warning` in a stream nobody reads. Add:

- a boot refusal or an unmissable startup banner on both sides
  (`brain/main.py:154`, `modules/tracker/main.py:95`) when the token is empty;
- the token in the setup checklist as required, next to the enrollment secret;
- a self-check the owner can run — *"is anything actually reaching me?"* — as a
  `make` target that pushes a real event end to end.

Also worth guarding: `.env.example` ships
`MODULE_EVENT_TOKEN=change-me-generate-a-strong-token`, which is non-empty, so
`if not settings.module_event_token` never fires on a `.env` copied from the
template. The check should reject **placeholder** values, not just empty ones —
the same defect as item 17's `${VAR:?}` note.

**16 — two assistants on one bot token.** The retry loop in
`agents/kaya/main.py:246` is correct; the silence about *why* is not. On repeated
`TelegramConflictError`, say the actionable thing **once**: the same token is
being polled elsewhere, and Telegram permits exactly one long-poll consumer.
Better still, make a second environment obvious at setup — a distinct token, or a
documented "one live instance" rule the scaffold states out loud.

**17 — setup papercuts.** Kaizen's own Makefile is already clean and skill §10
already demands `--env-file` on added stacks. What is missing:

- **Compose substitutes from the shell environment too**, and it wins over the
  file. A variable exported in the operator's shell silently overrides the
  intended value. One line in `docs/agents/secrets-and-env.md`.
- **The vendored-kit refresh is a development step written as a deployment
  step.** Its guard fails on any host without a Kaizen checkout and its default
  path is one developer's home directory — while the vendored copy is committed,
  so a deploy host needs nothing. Say so in the error message: *"you probably do
  not need this."* **Keep the guard** — it runs `rm -rf` before it copies, so
  without it, running on the wrong host destroys the vendored kit and then fails
  to replace it.
- **Production `up` must refuse a template `.env`.** Skill §5.12 currently says
  `.env` is generated from `.env.example` with fake values — right for
  development, wrong for production, because `${VAR:?}` catches *unset*, not
  *placeholder*. Split the behaviour: dev generates, prod refuses, and refuses
  **specifically at placeholder values**.

---

### Step 6 — the container owns the owner's repo (item 7)

Symptom: `error: insufficient permission for adding an object to repository
database .git/objects`, then `fatal: failed to write object`. The owner cannot
commit, fetch or pull in their own checkout. `sudo chown -R` fixes it until the
next Directive commits.

Two real options; **this needs the owner's decision** (§3):

- **Run the container as the host uid/gid.** Needs `HOME` moved somewhere the
  non-root user can write (the state volume) and a one-time `chown` of that
  volume, because it is created root-owned. Small, ships now.
- **A worktree per Directive** (Step 7). Larger, and it dissolves this item
  rather than patching it.

Whichever is chosen, the current situation — the owner periodically locked out of
their own repository, with an error that names none of this — must not ship to a
second project.

---

### Step 7 — a worktree per Directive (item 8) — scope separately

The largest item, and the one that subsumes several others.

Symptom: `MAX_CONCURRENT: 1`; the owner cannot switch branches while the fleet
runs; leftover task branches and modified files block a `git checkout` with an
error about an unrelated log file.

Cause: one working tree, shared. Two Directives would interleave their diffs, so
the concurrency cap is a workaround, not a design.

Fix: a git worktree per Directive under `.claude/worktrees/<directive-id>/`,
removed when the Directive ends. The owner's tree stops being the fleet's
workspace, concurrency stops being capped at one, and item 7 largely dissolves.

**Do it before the fleet gets faster, and scope it as its own task** — it touches
`wardenkit`, the compose mounts, the git workflow and every persona's assumption
about where it is standing.

---

## 3. Decisions the owner has to make

1. **Item 7 — uid/gid now, or wait for worktrees (Step 7)?** Recommendation:
   ship uid/gid in Step 6 anyway. It is small, it stops the lockout today, and
   it stays correct after worktrees land.
2. **Item 8 — this cycle or next?** Recommendation: next, as its own task, but
   before any work that raises `MAX_CONCURRENT`.
3. **Item 15 — banner or boot refusal?** Recommendation: refuse on the tracker
   (a Hub that cannot reach the owner is useless), banner on Brain (it has other
   jobs).

---

## 4. Two things not to do

- **Do not port the pilot project's files verbatim.** Several fixes are shaped by the pilot project's
  layout — its zones, its persona names, its `docs/` structure. Take the defect
  and the reasoning; write the fix for the kit.
- **Do not fix item 3 by making the Warden parse the pipeline's output.** The
  answer belongs to whoever produced it. The Warden's job is translation:
  Directive in, command out, result back.
