---
name: new-project
description: Create a new project from zero — interview the owner about purpose, scope and stack, write a charter, then scaffold a repo skeleton with a tailored agent fleet (only the agents that project needs), rulebooks, e2e profile and compose stack. Use when the user wants to start a new project, spin up a PoC, or bootstrap a repo with agents.
---

# new-project — idea → charter → repo skeleton with its own fleet

<!--
WHAT: The birth sequence from docs/agents/project-factory.md §4: Phase A (charter,
      a conversation) and Phase B (scaffold, mechanical).
WHY:  the mechanical half used to live here as prose, and a model re-typed it for
      every project. The first real project proved the cost — a wrong instruction
      in this file was inherited faithfully, and fixing it meant re-scaffolding by
      hand with no way to tell which projects predated the fix. That half is now
      `infra/agentkit/render.py`. What is left here is what genuinely needs
      judgement.
HOW:  §1 interview → §2 charter gate → §3 zones → §4 render → §5 rulebooks →
      §6 the rest → §7 prove it → §8 hand off. The gate in §2 is not optional.
-->

You are running the birth sequence for a new project.

**Three rules that shape everything below:**

1. **Nothing is created until the charter is approved.** The gate is the cheapest
   thing in this system and it is what prevents a fleet building the wrong
   product overnight.
2. **You do not render by hand.** `infra/agentkit/render.py` owns the roster, the
   command filter, every `{{SLOT}}` and the verification. Your job is the
   interview, the zones, the rulebooks — and `project.json`, which is how you
   hand your judgement to the script.
3. **Run this from the Kaizen repo.** The renderer writes to `projects/<name>/`
   by default. That is a staging area: fine for the containerised fleet, which
   only ever sees `/repo`, but an interactive session opened there on the host
   reads *Kaizen's* `CLAUDE.md` and rulebooks as its own. Move the project out
   before working in it by hand.

Read `infra/agentkit/MANIFEST.md` before you start — it is the contract, and §5
lists the invariants neither you nor the script may break.

---

## 1. Interview — once, in one batch

Ask **all** of these in a single message, numbered. Do not drip-feed; do not
start work with half the answers.

**Product (these become the charter):**
1. What is it, in one sentence a stranger would understand?
2. Who is it for, and what do they do with it?
3. What is explicitly **out** of scope for this version? *(the most valuable answer)*
4. What does "done" mean — what must work for you to call it a success?
5. What stack, or "you pick"?
6. Where does it deploy, and does it need a public URL?
7. What data does it hold, and does any of it need to be real?
8. Budget ceiling — tokens, time, or both.
9. What may agents decide alone, and what must come back to you? In particular
   the **autonomy level**:
   - **L0 — review each PR** (default): a PR per task, you merge.
   - **L1 — batch**: agents work the queue, open PRs, merge nothing, and report
     **once per milestone** with every link.
   - **L2 — autonomous merge**: agents merge into the integration branch when
     every gate is green, and report once per milestone.
   At every level `main` is yours alone, and a blocked agent, a red gate or an
   exhausted budget interrupts you immediately — batching hides progress, never
   problems.

**Shape (these choose the fleet):**
10. How should agents work inside this project — **solo** (one agent does
    design and implementation; every task still closes with an independent
    second reviewer, `reviewer-strict`, via the review-loop skill — solo
    never ships on self-review alone) or **crew** (a zone-driven multi-agent
    fleet — architect, devs, reviewers, scaled to the zones below)?
    **Recommend solo by default** — most projects don't need real
    agent-to-agent parallelism, and every hop between agents is coordination
    cost paid in tokens, not work done. The review step is the one exception:
    it stays a separate, independent agent in both topologies, because that
    is the check that catches what the author already believes is correct.
    Crew earns its cost when the project has genuinely independent parts
    that benefit from parallel work. This is reversible either way:
    re-running the renderer with an updated `project.json` (`topology: crew`
    + real zones) grows a solo project into a crew later, and nothing about
    the tracker/Warden side has to change for that — it already runs one
    agent or several per Directive.

    **If solo: what should it be called?** Offer a few suggestions in the
    same "overseer" flavor the kit's other personas draw from — **Alfred**
    (the default if the owner has no preference), **Jarvis**, **Friday**,
    **Gandalf**, **Hagrid** — or take any name the owner gives. This is
    purely the display name: the underlying file stays
    `.claude/agents/alfred.md` and the technical slug stays `alfred`
    regardless (the tracker's join key, `subagent_type`, status pushes —
    none of that is worth re-deriving per project). Record the choice as
    `"solo_name"` in `project.json` (§4); omit it for the default.
11. Which parts does it have? (service / web UI / mobile / CLI / worker / infra —
    or let me propose a split from answer 1). Matters for both topologies:
    even in solo, zones organize `CLAUDE.md`'s path/rulebook/verify table —
    they just don't fan out into one agent each.
12. Repo name, and where it finally lives? Private remote now, or local only? If
    there is a remote: may the fleet push to it — which needs a repo-scoped
    token — or do you push by hand?
13. Do you want the product layer (`/product`, charter + backlog + accept), or
    will you drive every task yourself? **Solo projects never get `/product`**
    regardless of this answer — it is a Product Owner handing scope to an
    architect, which is structurally two agents talking.
14. Anything the agents must never do in this project?
15. Should it join the Kaizen tracker, and at which tier? **Warden** (gRPC
    daemon: the Hub dials it, per-agent status in the panel, `AskOwner` blocks
    until you answer, crashes requeue the work), **poller** (~30 lines of HTTP,
    final result only), or **neither for now**? Default: Warden if the project
    has a fleet doing real work, neither if it is a one-off script.

**Defaults you may assume without asking** (state them in the summary): `main` +
`develop` branches, `docs/tracker` as tracker root, one e2e scenario per
acceptance criterion, reports in the owner's language, personas from the kit's
name list, `MAX_CONCURRENT=1`.

If the answer to 5 is "you pick", propose a stack in the charter with one line of
reasoning per choice — do not go and research it unless asked.

---

## 2. Charter → approval gate

Write `charter.md` **in the scratchpad, not in a new repo** — the repo does not
exist yet:

```markdown
# {project} — charter

## One-liner            {answer 1}
## Users and jobs       {answer 2}
## Non-goals            {answer 3, as a list}
## Success criteria     {answer 4, each as Given/When/Then}
## Stack and deployment {answers 5–6}
## Data                 {answer 7, incl. what must never be real}
## Budget               {answer 8, a number with a unit}
## Autonomy             {answer 9: decide-alone list, always-ask list, and the
                        level — L0 / L1 / L2 — written out in full}
## Topology             {answer 10: solo or crew, and why}
## Zones                {from answer 11 — see §3}
## Fleet                {what the roster will come out as — see §3}
```

Show the owner: the one-liner, the non-goals, the zone split, the fleet size, and
the defaults you assumed. Then **stop and ask for approval.**

Rejected or amended → revise and ask again. Nothing below runs before a yes.

**L2 is never inferred.** If the owner did not ask for autonomous merging in so
many words, write `L0`. "They said they trust the fleet" is not a grant.

---

## 3. Zones — the one structural judgement

**A zone** is: a key, a label, a path set, a rulebook, a verification command.
Derive them from answer 11 — typically one per deployable part (`api`, `web`,
`worker`, `infra`). Zones matter for **both** topologies — even in solo they
organize `CLAUDE.md`'s path/rulebook/verify table — but only crew fans them
out into one agent per zone.

**Fewer zones is better.** Two agents that must coordinate constantly belonged in
one zone. No two zones may own overlapping paths — the renderer checks this
pairwise and refuses.

**If answer 10 was solo, skip this paragraph** — the roster is Alfred plus
the always-installed `reviewer-strict`, and `build_roster()` returns it
before any of the below runs (`MANIFEST.md` §2).
Otherwise the **roster follows mechanically** and the script derives it
(`MANIFEST.md` §2): one dev per zone, a lead only where a zone has ≥2 devs or
the project has ≥3 zones, reviewers, security and architect always, the PO
only if answer 13 was yes. You do not pick names or models — say what you
expect the count to be in the charter, and let the renderer print what it
actually chose.

A 4–6 agent fleet is a good outcome for a PoC that genuinely wants a crew;
solo is the better default for most everything else.

---

## 4. Write `project.json`, then render

Everything the script needs, and nothing it can work out itself:

```json
{
  "project": "myproj",
  "purpose": "One line: what this is for.",
  "owner_language": "Russian",
  "main_branch": "main",
  "integration_branch": "develop",
  "repo_url": "https://github.com/owner/myproj.git",
  "max_concurrent": 1,
  "autonomy": "L0",
  "topology": "crew",
  "solo_name": "Jarvis",
  "product_owner": true,
  "researcher": true,
  "analyst": false,
  "design": false,
  "e2e": false,
  "deploy": false,
  "kinds": ["develop","fix","refactor","review","epic","brainstorm","research","ask"],
  "zones": [
    {"key": "api", "label": "HTTP API", "paths": ["api/**"],
     "verify": "make test-api", "rulebook": "01-api-rulebook.md", "devs": 1}
  ],
  "verify_all": "make test-api",
  "doc_convention": "docstrings",
  "slots": {
    "SECURITY_ZONE_NOTES": "- the HTTP API is the only public surface"
  }
}
```

`slots` is where **authored** values go (`MANIFEST.md` §4) — anything the script
cannot know. It has defaults for the derivable ones; whatever is left shows up as
an unresolved `{{SLOT}}` and the render refuses.

**`solo_name`** only matters when `topology` is `solo` — it's shown here
alongside `crew`'s fields for completeness, but `build_roster()` ignores it
unless solo is actually chosen. Omit it for the default (`Alfred`).

**`"e2e": true`** wires up agent-run e2e for a Warden project: forces
`dind: true` (below), copies `docs/e2e/command/e2e.md` to
`.claude/commands/e2e.md`, and pre-fills `.e2e/profile.yml`'s `boot.*` block
against this project's own `deploy/docker-compose.yml` — see
`infra/agentkit/MANIFEST.md`, "E2E". Default off; ask the owner (it's not one
of the 15 interview questions above, since most PoCs don't need it yet — offer
it when the project clearly wants real e2e coverage, or when they ask directly).

**`"deploy": true`** installs `/deploy` (no persona spawn — a `git`/`gh`
sequence) and writes `.github/workflows/deploy.yml`, so the owner can tell
Кая "deploy `<project>`'s changes" and have it open a PR into the project's
own `deploy` branch for a self-hosted GitHub Actions runner to pick up — see
`infra/agentkit/MANIFEST.md`, "Deploy". Default off, same reasoning as e2e:
most PoCs have no prod host yet. Ask when the project already has (or is
about to get) somewhere real to run — not one of the 15 interview questions,
offer it when it comes up. If `project.json` sets an explicit `kinds` list
(rather than leaving it to default to every kind), remember to add
`"deploy"` to it, or the Warden never advertises the capability even though
the command exists.

**`verify_all` must never be `make test`.** It is rendered into the body of the
Makefile's own `test` target, so that value recurses forever — the renderer
refuses it. Give the real commands (the zones' verifies chained with `&&`), or
omit it and let the renderer chain them for you.

```bash
python3 infra/agentkit/render.py project.json
```

It writes `projects/<name>/`, then verifies: no unresolved slots, no overlapping
zone paths, no command spawning a persona that does not exist, every persona's
`name:` matching its filename. It is idempotent — re-run it after any spec change
rather than editing rendered files.

**Read its output to the owner**, especially the "Not installed" line: "no
`/design`: this project has no designer" tells them you chose rather than forgot.

**Never hand-edit a rendered file.** The kit is a moving target and can gain
sections *during* a scaffold. A re-render is one command and a diff; hand edits
are archaeology, and the failure is silent — the project keeps the old kit's
behaviour and nobody can tell later which projects predate a fix.

---

## 5. Author the rulebooks — the part no script can do

`projects/<name>/.claude/projects/` is empty except for what you write. The
personas already point at these files by name; a persona pre-reading a rulebook
that does not exist will either invent its rules or stop.

- `00-core-invariants.md` — from answers 1, 3, 7, 14
- one rulebook **per zone**, numbered from `01-` in zone order and skipping `03-`
  (reserved): stack, layout, idioms, the interfaces the zone exposes, testing,
  verification, traps (from answer 5)
- `03-security-checklist.md` — universal items plus what this product actually
  holds (answer 7)

Write only rules that would change what an agent does. "Write good code" is not a
rule; "every handler returns the typed error envelope, never a raw string" is.

**Stack knowledge goes here and nowhere else.** A persona carrying a stack rule
inline cannot be reused, which is the whole reason the kit exists.

---

## 6. The rest — what the renderer does not do

In this order:

1. **Move it out of Kaizen** if the owner will work in it by hand:
   `mv projects/<name> ~/<name>`. Then `git init -b {main branch}` — the
   scaffold, README fill-in, port block etc. (steps below) all happen on this
   branch, before the first commit. Do not create `develop` yet — that comes
   after step 9's commit, once there is something on `main` to branch from.
2. **Source layout**: one directory per zone, matching the path sets exactly.
3. **Fill in the stubs the renderer wrote**: `README.md` and `CLAUDE.md` carry
   the zone table and the fleet already; add how to run and verify it. Every
   zone's verify command must actually run, even as a stub that exits 0 with
   "no tests yet" — an agent will trust it and hand over a broken build.
4. **Published ports, if it has services.** Never publish a default port:
   `5432`, `6379`, `5672`, `27017`, `9200`, `3000`, `8080` are taken on any
   machine already running one other project — including Kaizen, whose dev
   overlay publishes `127.0.0.1:5432`. The collision shows up as a container
   that will not start, or worse, an app that talks to *the other project's
   database*. Inside the compose network services keep standard ports; published
   ones come from a per-project block bound to `127.0.0.1`, checked free
   (`lsof -iTCP:<port> -sTCP:LISTEN`), driven from `.env` (`HOST_PORT_BASE`),
   and recorded in the README.
5. **`.e2e/profile.yml`**, if `project.json` had `"e2e": true`. The renderer
   already wrote `.claude/commands/e2e.md` and pre-filled the profile's
   `env.mode`/`boot.up`/`down`/`timeout` (`infra/agentkit/MANIFEST.md`, "E2E").
   `boot.ready`, `boot.reset`, `run.*`, `needs` and `boundary` are still
   placeholders — run `/e2e <a real scenario for this project>` now, in this
   session, and let its own interview (docs/e2e/command/e2e.md §1) fill them
   in. Do not hand-write those answers yourself; the point of the interview is
   that it happens once the actual test runner and boot ordering are known,
   not guessed at scaffold time. If `"e2e"` was `false`, there is nothing to do
   here — say so rather than leaving a silent gap the owner assumes was
   forgotten.
6. **Copy the approved `charter.md`** to `docs/product/charter.md`.
7. **The token, if the fleet pushes.** Tell the owner to create it themselves:
   fine-grained, this repo only, `contents: write` + `pull_requests: write`
   (plus `workflow: write` only if agents will edit CI). Never ask for a classic
   org-wide token, never ask them to paste it into the chat, never write it
   anywhere but their own `deploy/warden/.env` (the Warden's own env file — the
   project root's `.env` is for the app, not the fleet). If they decline, the
   project stays local-only and reports a file list instead of a PR — say so
   rather than half-wiring it.

   The wiring itself is already rendered: the entrypoint stores the token in
   `gh`'s own config with `env -u GH_TOKEN gh auth login --with-token`, writes
   `~/.git-credentials`, and then `exec env -u GH_TOKEN` — so no agent can
   `printenv` it. Do not "simplify" that: `env -u` is required (gh refuses to
   store a token while the variable is set), and the failure must stay
   non-fatal, because a hard exit under `restart: unless-stopped` becomes a
   restart loop that reads as a broken image.
8. **First boot, then the one-time login.** Both stacks are run from their own
   directory: `cd deploy/warden && make up` for the fleet, plain `make up` from
   the repo root for the app once it exists. `deploy/warden`'s `make up` chowns
   the state volume before starting (it is created root-owned, and the
   container runs as the owner's uid — started any other way, the entrypoint
   refuses with the exact chown command). Then `make login` once, still from
   `deploy/warden/`: the `claude` CLI needs a `/login`, the credential lands in
   the state volume (`CLAUDE_CONFIG_DIR=/state/claude`) and survives restarts.
   **Until it exists, every Directive fails with "Not logged in"** — say this
   to the owner rather than letting them find it in a failure report.
9. **First commit, then branch to `develop`.** `.gitignore` covers `.env`
   (both the root's and `deploy/warden/`'s) before any commit happens.
   `deploy/warden/.env` is generated from its `.env.example` the first time
   `make up` runs there; the root `.env` isn't auto-generated until
   `deploy/docker-compose.yml` exists (its `make up` refuses before that
   point) — copy it by hand meanwhile, same as the README says.
   ```bash
   git add -A && git commit -m "chore: scaffold {project}"
   git checkout -b {integration branch}
   ```
   Leave `develop` checked out — it's where `/product` or the first
   `/develop` starts (§8), and every PR the fleet opens targets it. `main`
   stays exactly at this scaffold commit until the owner releases into it by
   hand.

### The question gate — decide it before the first dispatch

This is the failure that costs whole nights, and it is silent. `/develop` Phase 1
and `/product` Phase 1 **stop and wait for the owner's answers** — correct when a
human is in the session, fatal for an unattended run through a Warden, where the
run halts at the gate, produces no artifact, and looks like a slow agent rather
than a design mismatch.

Pick one and write it in the Warden's README, because whoever debugs a "stuck"
run will look there first:

- **Wire it (preferred).** Make the owner channel reach `job.ask()`, which blocks
  until the owner answers or the timeout lapses. Files are the simplest
  transport: `ask_owner.sh` writes the question into the task directory and polls
  for an answer file; the Warden watches for it and writes the reply back.
- **Or forbid the stop.** The dispatch prompt states: nobody can answer, do not
  wait; take the most conservative option, record it under `Assumptions`, and
  deliver the artifact. Never phrase it as "stop and report rather than
  guessing" — that is an instruction to hang.

### Kinds — three traps that have all bitten

The Hub's vocabulary is fixed: `develop · fix · refactor · research · review ·
epic · brainstorm · analyze · ask`. The renderer maps them, but check the result:

- **`brainstorm` is planning.** With a PO it routes to `/product` — "what is
  worth doing and what do we start with" is Ohno's question. `/brainstorm` puts
  the architect in business-analyst mode and bypasses whoever owns the backlog.
- **`review` is a CODE review**, not "review my plan". A planning ask filed under
  it produces a confident review of somebody else's commit.
- **`ask` is a conversation.** It runs a persona, not a pipeline: no fleet, no
  files, no PR. The rendered `run_ask` returns the model's answer as the summary,
  diffs the tree, and reports `review` instead of `done` if anything changed.
- **A kind whose command is not installed is refused by name**, with a reason.
  Routing it to something adjacent is worse than failing.

**`epic` must return `children`, or it decomposes into nothing.** The pipeline
writes `docs/tracker/{task_id}/children.json` — a JSON array of
`{title, intent, kind}` in dependency order. Parse the file, never the prose: the
Hub queues whatever comes back, so a formatting slip in markdown becomes real
queued work. No file → report "nothing was queued", do not invent boundaries.

---

## 7. Prove it — do not report success on a fleet that has never run

1. **Each zone's verify command**, actually executed.
2. **`cd deploy/warden && make up`**, then the readiness check from
   `.e2e/profile.yml` (its `boot.up`/`down` run the app's own stack from
   inside the fleet container — see `infra/agentkit/MANIFEST.md`).
3. **The scenario sheet, if there is a Warden.** Zone verify commands say nothing
   about whether Directives arrive, and every integration failure here is silent
   — a Directive accepted that did the wrong thing. Write
   `deploy/warden/scenarios.py` + `make warden-scenarios` driving the real Hub:

   | scenario | what must hold |
   |---|---|
   | dispatch → report | reaches a terminal state, not limbo |
   | cancel mid-flight | ends `cancelled`, the child process dies |
   | at capacity | the second Directive queues; never lost or failed |
   | unsupported kind | refused by name, not routed to something adjacent |
   | epic | children are queued **under** the parent |
   | restart `scope: jobs` | in-flight work is dropped **and requeued** |
   | restart again inside 60s | refused, with a reason — that refusal is correct |
   | status relay | a status file written by an agent reaches the Hub in ~5s |

   Make it free to run: a `WARDEN_FAKE_PIPELINE=1` switch whose handler keys off
   words in the intent (`hang`, `fail`, `block`, `quick`), exactly as
   `modules/tracker/example/dummy-project` does. Without that, every assertion
   costs a real fleet run and the sheet gets written once and never run again.

   Two mistakes to expect: the Hub assigns `task_id` at **dispatch**, so reading
   it at creation gives `NULL` and your probe lands in `docs/tracker/None/`; and
   live status is `tracker_agent_status`, not `GET /agents` — that endpoint is
   the roster, and confusing them reports a working relay as broken.
4. **One real task end to end** — an `ask` first (cheapest), then the smallest
   genuine piece of work. This is what catches the question gate, a persona
   pre-reading a file you never wrote, and a verify command that only passes in
   your shell.
5. **`make notify-selftest` in Kaizen** — reports and questions reaching the
   owner is the one path nothing else exercises.

**Ask before each outward-facing step — never bundle them into the §2 approval:**

- **creating the remote and pushing.** Private
  (`gh repo create <owner>/<name> --private --source .`), using *your own*
  `gh` auth — the project's `GH_TOKEN` is for its fleet, not for this step.
  `develop` is the branch left checked out from §6.9, so a plain `--push`
  here would push only `develop` and make it the remote's default branch.
  Push both explicitly and keep `{main branch}` the default:
  `git push -u origin {main branch} {integration branch}` — the remote's
  default branch is whichever `gh repo create` picked up from the local
  checkout at creation time, so if it did not land on `{main branch}`, set it
  with `gh repo edit <owner>/<name> --default-branch {main branch}`. A pushed
  repo cannot be un-pushed.
- **enrolling the Warden.** The project registers and waits; the owner approves
  deliberately (`make approve`, the panel, or Кая). Never auto-approve — the
  enrollment secret is what proves the project claiming approval is the one that
  asked.
- deploying or starting anything on a host.

---

## 8. Hand off

Report:
- where the repo is, and the tree one level deep
- the zone table, and the renderer's fleet output verbatim — including what it
  did **not** install and why
- which verify command proves each zone
- what is a stub and will fail honestly until filled — never present a scaffold
  as a working product
- what actually ran in §7, and what you did not run
- the scenario sheet result (`N/N`) and the outcome of the one real task,
  including the question gate: wired to `job.ask()`, or proceeding under stated
  assumptions
- **how this project pushes**: remote URL, whether the fleet has a token, the
  branch model. No token yet → say plainly that it reports a file list rather
  than a PR until one exists
- **the published port block**
- the exact next step: open a session **in the new repo** (the fleet lives in
  *its* `.claude/`, not Kaizen's), then `/product` if the PO was installed,
  otherwise `/develop <first task>`

---

## Failure modes to avoid

- **Skipping the gate** because the answers "seemed clear". One message, versus a
  night of wrong work.
- **Rendering by hand, or editing rendered files.** The kit changes, sometimes
  mid-scaffold. Change `project.json` and re-run.
- **Writing stack knowledge into personas.** It goes in rulebooks.
- **A verify command that does not run.** An agent will trust it.
- **Doing the outward steps unasked.** A pushed repo cannot be un-pushed.
- **Publishing default ports.** Works perfectly until the second project — then
  it either refuses to start or silently talks to the first project's database.
- **Putting a token anywhere but `deploy/warden/.env`.** Not in compose, not in
  the README, not in a persona. If one is ever printed, it is burned and must
  be rotated.
- **Believing a secret arrived because you put it in `.env`.** "It's in the file"
  and "the process has it" are different claims. Boot it and make it prove it.
- **A compose stack added without `--env-file`.** The first one usually has it;
  the third, added a week later in a subdirectory, does not — and reads a `.env`
  that is not there. Every variable becomes empty and nothing complains.
- **Handing over a fleet whose question gate has never been exercised.** A
  pipeline that stops for an answer nobody can give is indistinguishable from a
  slow one, and will be blamed on the model, the quota or the machine for hours.
- **Declaring a `kind` you cannot run.** The Hub sends work nobody handles.
- **Routing every kind through a pipeline.** Asking a project a question is not
  commissioning work. If "what's the status?" spawns an architect, a developer
  and a review loop, the owner stops asking — and the one channel that made the
  fleet legible goes quiet.
- **Reporting a path instead of the answer.** The owner reads the summary on a
  phone. A diffstat or a filename there is not a result; it is homework.
