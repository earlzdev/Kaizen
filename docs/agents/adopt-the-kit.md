# How do I put this agent fleet into a project that already exists?

<!--
WHAT: The adoption path for a repo that already has code — the one case
      /new-project cannot handle, because it refuses a non-empty directory.
WHY:  the kit is written for projects created from it. An existing repo already
      has a layout, a build, and habits; adopting the fleet means DESCRIBING
      those, not replacing them. Most of the work is reading, not writing.
HOW:  hand this file to an agent working inside the target repo. It is written to
      be self-contained — it tells that agent what to copy and what to decide.
STATUS: written, unproven. No repo has been adopted with it yet.
-->

**You are an agent inside a repository that already has code.** Your job is to
install an agent fleet that fits *this* repo — not to reshape the repo to fit the
fleet.

The one rule that governs everything below: **zones and rulebooks describe what
is already here.** If you find yourself proposing a directory move, you have
misunderstood the task.

---

## 0. What you are installing

| Piece | Goes to | What it is |
|---|---|---|
| personas | `.claude/agents/*.md` | the fleet: architect, devs, leads, reviewers, security, and optionally a Product Owner |
| commands | `.claude/commands/*.md` | `/develop`, `/fix`, `/refactor`, `/epic`, `/review`, `/research`, `/doc`, `/next`, and `/product` if there is a PO |
| workflow | `.claude/workflow.md` | zones, branch model, agent registry, the phases |
| git rules | `.claude/git-workflow.md` | branch/PR/merge rules and the autonomy level |
| rulebooks | `.claude/projects/*.md` | **this repo's** invariants, per-zone rules, security checklist |

The personas are deliberately **stack-free**: they carry the procedure, and every
project-specific value is a `{{SLOT}}` you fill. Stack knowledge goes in the
rulebooks. A stack rule inline in a persona is a bug.

## 1. Get the kit

Source: `infra/agentkit/` in the Kaizen repo.

- Same machine → copy from there: `cp -r <kaizen>/infra/agentkit /tmp/agentkit`
- Otherwise → clone Kaizen and copy from the clone.

**Read `infra/agentkit/MANIFEST.md` first.** It is the rendering contract: which
agents a project gets, what every slot means, and the invariants you must not
break. Everything below is the short version of it.

## 2. Derive the zones from the repo you have

A **zone** is a unit of ownership: a key, a label, a path set, a rulebook, a
verification command, and exactly one owner at a time.

Read the repo and write down what already exists — `services/api/**` is a zone
because it is a deployable thing with its own tests, not because the kit has an
`api` example. Rules:

- one zone per deployable part, following the existing directory structure;
- **path sets must not overlap** (check pairwise — this is what stops two agents
  editing one file);
- every zone needs a verification command **that already works in this repo**
  (`make test-api`, `pytest services/api`, `go test ./...`). Run each one before
  writing it down. If a zone has no test command at all, say so and use the
  build/lint command — never invent a target that does not exist;
- fewer zones is better. Two agents that must coordinate constantly belonged in
  one zone.

Confirm the zone table with the owner before going further. Getting this wrong is
expensive later: it is the boundary every task is split along.

## 3. Pick the roster

From `MANIFEST.md` §2 — the rules that matter most:

- architect and security: **always**
- one dev per zone, minimum one
- **a lead only when a zone has ≥2 devs, or the project has ≥3 zones** — a lead
  with one report is pure latency
- one reviewer per zone group
- designer + UI reviewer only with a UI **and** a design system
- the Product Owner only if the owner wants `/product` (business / R&D / devrel /
  an owned backlog). If they drive every task themselves, skip it
- researcher: cheap, install unless told otherwise

**A 4–6 agent fleet is a good outcome.** Installing nineteen in a repo that
builds one service costs tokens on every spawn and invents coordination.

Use the kit's identities in order, never invented names:
devs `anderson, neumann, wayne, potts, parker, barton, romanoff, kent`;
leads `tesla, torvalds`; reviewers `granger, mcgonagall`.

**Install a command only if every persona it spawns is installed** (`MANIFEST.md`
§2b): no designer → no `/design`; no PO → no `/product`; no tracker service → no
`/next`.

## 4. Render

Copy the chosen files into `.claude/` **keeping the kit filenames**
(`dev-anderson.md`, not `api-dev-anderson.md`) — the commands reference them as
`subagent_type`. Then fill every slot from `MANIFEST.md` §3. The ones that bite:

- `{{VERIFY_CMD}}` is the **zone's** command; `{{VERIFY_ALL_CMD}}` is the whole
  project's. Rendering the same string into both makes every developer verify the
  entire repo on each change, which is slow enough that they stop doing it.
- `{{TRACKER_ROOT}}` — `docs/tracker`, and **it must stay that** if this project
  will run a Warden (the kit that talks to Kaizen's tracker hardcodes the layout).
- `{{AUTONOMY_LEVEL}}` — render **L0** unless the owner explicitly asked for
  more. L0 = a PR per task, they merge. Never infer L2.
- `{{MAIN_BRANCH}}` / `{{INTEGRATION_BRANCH}}` — this repo's real branches.

`workflow.md` is **rendered, not copied**: delete the phases this project does not
have (no PO → Phase 0 and Phase 8 go; no leads → Phase 3 becomes one line). A
phase naming an agent that does not exist reads as "someone else handles this",
and work falls through it.

Then check, do not eyeball:
```bash
grep -rn '{{' .claude/ && echo "FAILED RENDER"   # must print nothing
```

## 5. Write the rulebooks — from the code, not from a template

This is the part that is not substitution, and in an existing repo it is mostly
**reading**:

- `00-core-invariants.md` — what must stay true everywhere: layering, ownership,
  data rules, the error contract, what is never done here.
- `NN-<zone>-rulebook.md` per zone (numbered from `01-`, skipping the reserved
  `03-`) — stack, layout, **the idioms this code already uses**, the interfaces
  the zone exposes, testing, verification, traps.
- `03-security-checklist.md` — the universal items plus what this product holds.

Derive them by reading the code and asking the owner about anything you cannot
tell from it. Write only rules that would change what an agent does: "write good
code" is not a rule; "every handler returns the typed error envelope, never a raw
string" is. A rule you invented that the codebase does not follow will produce a
fleet that "fixes" working code.

## 6. Verify before handing over

- [ ] no `{{` anywhere under `.claude/`
- [ ] no two zones' path sets overlap
- [ ] every zone's verify command runs, here, now
- [ ] every persona a command spawns exists
- [ ] each persona's `name:` frontmatter matches its filename
- [ ] `.claude/KIT_VERSION` written (copy `infra/agentkit/VERSION`)
- [ ] `.gitignore` covers `.env`

Then run **one** small task end to end (`/develop <something trivial>`) and watch
whether the task files land where the personas expect. Do not report success on a
fleet that has never run.

## 7. Optional — join Kaizen's tracker

Only if the owner wants this project visible in the Hub: add a Warden
(`infra/wardenkit`, see Kaizen's `docs/tracker-integration.md`). Two things fail
silently if you get them wrong:

- the manifest roster's `slug` must be **the persona filename verbatim**
  (`dev-anderson`), because live status joins on it;
- status files use `state:` and `updated_at:`, and the filename is the join key —
  `status/dev-anderson.yml`, never `anderson.yml`.

## Non-negotiables, whatever the repo

1. Never push/merge/PR to the main branch; never `git revert`; never force-push;
   never auto-resolve a conflict.
2. Developers commit inside their own zone; **only the orchestrator pushes**.
3. Auto-merge is opt-in per task. `main` is never agent-merged.
4. Secrets live in `.env`, which is never committed, never printed, never read
   into an agent's context. A token for a fleet is repo-scoped and fine-grained.
5. Published ports never use a service's default (`5432`, `6379`, `3000`, …) —
   the host already runs something. Inside the compose network, defaults are fine.
6. Silence is for progress, never for problems: a blocked agent, a red gate or an
   exhausted budget interrupts the owner immediately, at any autonomy level.

## What NOT to do

- Do not restructure the repo to match the kit's examples.
- Do not add a test framework, a linter or a CI file the repo does not have —
  note the gap and let the owner decide.
- Do not install the full roster "to be safe".
- Do not write a rulebook by copying another project's; it is the one file that
  must be about **this** code.
