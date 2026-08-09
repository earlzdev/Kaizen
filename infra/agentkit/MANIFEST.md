# agentkit — the contract between the kit and the scaffolder

<!--
WHAT: The rules for turning these templates into one project's `.claude/` fleet:
      which agents a project gets, what every {{SLOT}} means, and what must NOT
      be improvised.
WHY:  the personas are deliberately stack-free so one kit serves every project.
      That only holds if the rendering is deterministic — otherwise each project
      drifts into its own dialect and the kit stops being upgradeable.
HOW to read it: §1 is what gets rendered where. §2 picks the roster. §3 is the
      slot dictionary — the load-bearing part. §4 is what the skill must author
      rather than substitute.
-->

Consumed by the `new-project` skill. Kit version: see `VERSION`.

---

## 1. What renders where

```
infra/agentkit/                      →  <new-project>/
  agents/*.md      render slots      →    .claude/agents/*.md      (only the chosen ones)
  commands/*.md    render slots      →    .claude/commands/*.md    (only the viable ones, §2b)
  skills/*/SKILL.md render slots     →    .claude/skills/*/SKILL.md (every skill, unconditionally)
  workflow.md      render slots      →    .claude/workflow.md
  git-workflow.md  render slots      →    .claude/git-workflow.md
  deploy-workflow.yml render slots,  →    .github/workflows/deploy.yml
                   spec["deploy"] only    (write_deploy_files(), §3 "Deploy")
  rulebooks/*.md   author per project →    .claude/projects/*.md
```

Every `{{SLOT}}` must be resolved. A rendered file containing `{{` is a failed
render — fail loudly rather than shipping a persona with a hole in it.

One exception to "renders from `infra/agentkit/templates/`": when
`spec["e2e"]` is set, `docs/e2e/command/e2e.md` →
`.claude/commands/e2e.md` and `docs/e2e/profile.template.yml` →
`.e2e/profile.yml` — see "E2E" in §3. Those live in `docs/e2e/`, not the
kit, because the method is a project-agnostic export shared with non-Kaizen
projects too (`docs/e2e/README.md` §2).

**Two stacks, two Makefiles, two `.env` files.** Every scaffolded project gets
`Makefile` at the root (this project's own app — `make up [dev|prod]` against
`deploy/docker-compose.yml`, authored per-project, see the DinD section below)
and `deploy/warden/Makefile` (the Warden dev-fleet that BUILDS this project —
`cd deploy/warden && make up [dev|prod]`). They are independent on purpose:
`.env`/`.env.example` at the root holds the app's own variables,
`deploy/warden/.env`/`.env.example` holds the fleet's (`GH_TOKEN`,
`MAX_CONCURRENT`, …) — a fleet building a project should not need the app's
secrets, and an app should not need the fleet's. `deploy/warden/docker-compose.yml`
mounts BOTH real `.env` files' templates over their real counterparts inside
the container (`/repo/.env` and `/repo/deploy/warden/.env`), since the
`../..:/repo` bind mount would otherwise expose the Warden's own real secrets
to the agent it runs.

---

## 2. Choosing the roster

**Solo topology skips this whole section** for the DEVELOPMENT roster.
`spec["topology"] == "solo"` means the working roster is one entry — slug
`alfred` (`infra/agentkit/agents/alfred.md`), `tier: architect`, no zone, no
PO, display name `spec["solo_name"]` or `Alfred` if unset (the slug and the
file stay `alfred` either way — SKILL.md §1 suggests Jarvis/Friday/Gandalf/
Hagrid as alternatives, or any name the owner gives). Zones still exist in a
solo project (paths, rulebooks, verify commands for `CLAUDE.md`'s zone
table) but nobody owns just one — the solo persona owns all of it.
`/product` and `/design` are never offered in solo (both require two
personas checking each other, structurally). Every other command is
available; each command template's `{{SOLO_NOTE}}` tells the one persona to
do every phase itself instead of spawning.

**One deliberate exception: `build_roster()` also always installs
`reviewer-strict`** (`infra/agentkit/agents/reviewer-strict.md`) for solo —
so the roster is two entries, not one. This is not a second developer; it
has no `Write`/`Edit` in its `tools:` frontmatter at all, cannot touch code,
and exists solely so the review-loop skill's solo path spawns a persona that
is *structurally* unable to fix what it finds, instead of a fresh instance
of `alfred` merely told not to (a prompt instruction the same model could
talk itself out of — see "Review loop" below and `docs/decisions.md` in any
project that hit this: first noticed as a real gap, not designed in from the
start). `verify()`'s missing-persona check keys off `"alfred" in slugs`, not
slug-set equality, precisely because this second entry exists.

**Solo is the
default recommendation** — most projects don't need real agent-to-agent
parallelism, and every hop between agents is coordination cost paid in
tokens, not work done. Choose crew when the project has genuinely independent
parts that benefit from parallel work.

**Crew topology: zones drive everything.** A zone is: a key, a label, a path
set, a rulebook, a verification command. The interview produces the zone
list; the roster follows from it mechanically.

| Agent | Installed when |
|---|---|
| `architect-xavier` | always |
| `security-holmes` | always |
| `dev-*` | one per zone, minimum one. A zone with more work than one agent can hold gets a second dev **in the same zone only if their paths do not overlap** |
| `lead-*` | only when a zone has ≥2 devs, or the project has ≥3 zones. One lead per zone group. **Skip leads in small projects** — a lead with one report is pure latency |
| `reviewer-*` | one per zone group; a second only when two zones are unrelated enough that one reviewer cannot hold both |
| `product-owner-ohno` | only when the project wants `/product` (business / R&D / devrel / unattended backlog). Not needed when the owner drives every task |
| `analyst-lovelace` | only when the project wants living specs |
| `researcher-curie` | cheap; install unless the owner says no |
| `designer-davinci`, `ui-reviewer-rams` | only when there is a UI **and** a design system |
| `dev-potts` (infra identity) | only when deployment/CI is a zone of its own |

Names are assigned from the kit's identities, in order, and never invented:
`anderson, neumann, wayne, potts, parker, barton, romanoff, kent` (devs),
`tesla, torvalds` (leads), `granger, mcgonagall` (reviewers).

A four-agent project is a good outcome. Installing nineteen agents in a PoC costs
tokens on every spawn and produces coordination for its own sake.

### 2b. Commands depend on personas — install accordingly

A command that spawns an agent the project does not have fails at the worst
moment: mid-pipeline, after the owner has been told work started. **Install a
command only when every persona it spawns is installed.**

| Command | Requires |
|---|---|
| `/develop`, `/fix` | architect, security |
| `/refactor`, `/epic`, `/brainstorm`, `/next`, `/review`, `/abort`, `/doc` | architect (none for `/doc`, `/abort`) |
| `/research` | architect, researcher |
| `/analyze` | architect, analyst |
| `/design` | architect, designer, ui-reviewer |
| `/product` | product-owner, architect (delegates to researcher / analyst / designer **if present** — the command must say so rather than assume) |
| `/deploy` | none — no persona spawn, a single `git`/`gh` sequence. Installed only when `spec["deploy"]` is set (§3 "Deploy"), regardless of topology |

Reviewers and leads are spawned by name discovered from the task files at
runtime, not hardcoded in the commands, so they need no entry here.

`/next` additionally requires a **tracker service** (it pops the queue). Without
one it has nothing to pop — do not install it, and do not render a half-working
version whose queue commands point at a service that is not there.

---

## 3. Slot dictionary

### Identity of the project
| Slot | Value | Example |
|---|---|---|
| `{{PROJECT}}` | project name, as the owner says it | `banana-shop` |
| `{{MODEL}}` | model for this persona. **`opus` for architect / developers / PO; `sonnet` for leads, reviewers, security and the researcher.** The subscription — not the machine — is what limits how often the fleet can run, and a single `/develop` spends several personas; seven of eight on opus ran the quota out constantly. Reviewers, security and research read and judge; architects and developers produce the thing being judged, and a cheap mistake there is paid back by everyone downstream | `opus` |
| `{{REASONING_EFFORT}}` | how hard the model thinks, **declared rather than inherited, and defaulting `low`**. The caveat belongs wherever this is set: low is right while the fleet writes plans, briefs and decisions, and **wrong once it produces code** — effort not spent writing is effort a reviewer pays back with interest. Raise it for the code-producing kinds, not globally | `low` (`medium` for `/develop`, `/fix`, `/refactor`) |
| `{{OWNER_LANGUAGE}}` | language the owner reads reports in | `Russian` |
| `{{MAIN_BRANCH}}` / `{{INTEGRATION_BRANCH}}` | branch model | `main` / `develop` |

### Paths
| Slot | Value |
|---|---|
| `{{TRACKER_ROOT}}` | where task/status files live — default `docs/tracker`. **Fixed at `docs/tracker` for any project with a Warden**: `infra/wardenkit/trackerfiles.py` hardcodes that layout, so another value makes the fleet write handoffs the Warden never sees |
| `{{PRODUCT_ROOT}}` | charter/backlog/decisions — default `docs/product` |
| `{{SPEC_ROOT}}` | living specs — default `docs/specs` |
| `{{RESEARCH_ROOT}}` | research output — default `docs/research` |
| `{{SURFACE_REGISTRY}}` | canonical screen registry — default `docs/specs/screen-registry.md` |
| `{{CONTEXT_DOCS}}` | the repo's orientation docs, as a bullet list — e.g. `README.md`, per-zone `AGENTS.md` |
| `{{RULEBOOK_CORE}}` | `.claude/projects/00-core-invariants.md` |
| `{{RULEBOOK_SECURITY}}` | `.claude/projects/03-security-checklist.md` |
| `{{RULEBOOKS}}` | every rulebook relevant to this agent, as a list. Zone rulebooks are numbered from `01-` in zone order, skipping the reserved `03-` |

### Commands (strings the agent executes — never concepts)
| Slot | Value |
|---|---|
| `{{VERIFY_CMD}}` | **zone-scoped**: how *this agent's* zone proves it builds and passes — `make test-api`. In a lead persona, render the list for every zone that lead covers |
| `{{VERIFY_ALL_CMD}}` | **project-wide**: verifies every zone — `make test`. Used by the commands, never by a dev persona |
| `{{NOTIFY_CMD}}` | how an agent messages the owner |
| `{{ASK_OWNER_CMD}}` | how an agent asks a **blocking** question |
| `{{TRACKER_CMD}}` | tracker CLI, when the project has one; otherwise render the command lines out of the file entirely rather than leaving a broken path |

### DinD (`spec["dind"]`, default off)

Two consumers share this slot. Some projects need a real Postgres/Redis/HTTP
for their own integration tests, and a Warden fleet has no other route to
one — it runs inside its own container, usually on a host that also runs
Kaizen and other projects. The second consumer is `spec["e2e"]` (below) —
agent-run e2e tests need exactly the same isolated docker engine, for the
same reason.
**Never give it the host's `/var/run/docker.sock`** — that mount is
host-root-equivalent, and a Warden is agent code steered by whatever the
project's own inputs turn out to be (worse still for a project whose product
*is* a public, untrusted-input endpoint). First tried and reverted in
an earlier project, 2026-08-05, the same day it was approved — the approval
had wrongly assumed a dedicated host.

Set `"dind": true` in `project.json` instead: `render.py`'s `dind_slots()`
adds a second service, `dind` (`docker:27-dind`, `privileged: true`, its own
volume, its own network `dind-net` that nothing else joins), and points
`warden` at it via `DOCKER_HOST=tcp://{{PROJECT}}-dind:2375` instead of a
socket mount. `docker`/`docker compose` need no code change — they already
just read `DOCKER_HOST`. Whatever the fleet starts (its own
`deploy/docker-compose.yml`, which is authored per-project, not part of this
kit — see §4) lives entirely inside `dind`'s isolated storage/network
namespace, with no route to the host's real daemon or any other project's
containers. `privileged: true` is unavoidable for nested docker, but it's a
boundary around `dind`'s *own* engine, not a bridge into the host's.

Off by default: most projects have no live-infra tests and every DIND_* slot
substitutes to `""`, leaving the rendered `Dockerfile`/`docker-compose.yml`
byte-identical to a non-DinD render — verified by diff when this was added.

### E2E (`spec["e2e"]`, default off)

`docs/e2e/README.md` is the design record; `docs/e2e/command/e2e.md` is the
universal `/e2e` method (stack-independent, copied byte-for-byte into every
adopting project — never rendered with `{{SLOT}}`s, since it must stay
identical across projects for `kit_version` drift-checking to mean anything).
`docs/e2e/profile.template.yml` is the per-project skeleton.

Setting `"e2e": true` in `project.json` does three things:

1. **Before anything is written** — forces `spec["dind"] = true` if it wasn't
   already (the DinD slots below depend on it). e2e's
   `boot.up`/`ready`/`reset`/`down` commands are plain `docker compose`
   strings the coding agent runs directly, because it already runs *inside*
   `warden`, the one container DinD wires up with `DOCKER_HOST`. No ssh, no
   separate execution channel, no new mechanism — e2e just turns on the one
   that already exists.
2. **After everything else is rendered** — copies `docs/e2e/command/e2e.md` to
   `.claude/commands/e2e.md`, and `docs/e2e/host/e2e_gen_secrets.py` to
   `.e2e/e2e_gen_secrets.py`, unchanged, on every render (both are kit-owned,
   safe to re-copy — see docs/e2e/README.md §8 for what the script does: it
   turns `GENERATE`-tagged keys in `.env.e2e.generated.example` into fresh
   random tokens and knows nothing else about the project).
3. **Same step, first render only** — writes `.e2e/profile.yml` from
   `docs/e2e/profile.template.yml`, with `env.mode` and the
   `boot.up`/`down`/`timeout` lines pre-filled (they're mechanical facts of
   the DinD wiring and the `deploy/docker-compose.yml` convention), plus
   starter `.env.e2e.generated.example` and `.env.e2e.live.secrets.example`
   skeletons the owner fills in with this project's own key names.
   `boot.ready`/`reset`, `run.*`, `needs`, `boundary` and those secrets keys
   stay placeholders — they depend on this project's health check, test
   runner and secrets, which no render script can know. The `/e2e` command's
   own interview (its §1) fills those in on first use. **A re-render never
   touches any of these four files once they exist** — unlike the method file
   and the secrets script, they are co-located project data
   (docs/e2e/README.md §1, §8), and an answered interview or a filled-in key
   list is not something a spec change should be able to erase.

`write_e2e_files()` in `render.py` does steps 2–3; `prefill_e2e_profile()`
does the placeholder substitution and fails loudly (`RenderError`) if
`profile.template.yml`'s wording ever drifts out from under it — a silent
partial fill would ship a profile that looks complete but boots nothing.
`e2e_secrets_slots()` wires the matching `make e2e-secrets`/`e2e-secrets-live`
targets, `.gitignore` lines and CLAUDE.md never-read rule into the project's
own templates, empty when `e2e` is off.

Off by default, same reasoning as DinD: most projects don't want agent-run
e2e, and forcing it on would mean every render pays for a `dind` sidecar and
several extra files it never asked for.

**Two traps the `/e2e` interview must account for, both consequences of the
stack running on `dind`'s own engine, not the host's:** a bind mount in
`deploy/docker-compose.yml` resolves against `dind`'s filesystem, not
`/repo` — a mount meant to share source or data between the fleet's checkout
and the booted stack silently sees nothing there. And a published port lands
on the `dind` container, never on `localhost` or `warden` — `boot.ready` has
to poll `http://{{PROJECT}}-dind:<port>/...`, or it can never succeed.

### Deploy (`spec["deploy"]`, default off)

Lets `/deploy` — and, via the Hub, a `deploy`-kind Directive — ship this
project's own current `{{MAIN_BRANCH}}` to a prod host: open (or, when
explicitly told, merge) a PR into `{{DEPLOY_BRANCH}}`, watched by a
self-hosted GitHub Actions runner (`{{DEPLOY_RUNNER_LABEL}}`) that redeploys
by running `{{DEPLOY_CMD}}`. Mirrors Kaizen's own pipeline
(`docs/deploy-pipeline.md`).

| Slot | Value | `project.json` key | Default |
|---|---|---|---|
| `{{DEPLOY_BRANCH}}` | branch the runner watches | `deploy_branch` | `deploy` |
| `{{DEPLOY_RUNNER_LABEL}}` | self-hosted runner label `runs-on` matches | `deploy_runner_label` | `{{PROJECT}}-prod` |
| `{{DEPLOY_CMD}}` | what the runner executes on push | `deploy_cmd` | `make up prod` (the root Makefile target every project already gets, §1) |

Setting `"deploy": true` in `project.json`:
1. Adds `/deploy` to the rendered commands (`pick_commands()`'s one
   spec-flag gate, alongside `/next`'s `tracker_cli` gate — not a persona
   requirement, since `/deploy` needs none).
2. Adds `"deploy"` to the advertised `KINDS` (§3 "Commands (strings...)" —
   whichever Directive kinds this project's Warden accepts), so the owner
   can tell Кая "tell `<project>` to deploy" the same way `develop`/`fix`
   already work. **Unless `project.json` sets an explicit `kinds` list** —
   then `"deploy"` must be added to it by hand, or the render picks the
   command but the Warden never advertises the capability (SKILL.md §4
   documents this same caveat for the interview).
3. Writes `.github/workflows/deploy.yml` from `infra/agentkit/deploy-workflow.yml`
   (`write_deploy_files()` — a top-level KIT file like `workflow.md`, not
   under `templates/`, since that tree renders unconditionally and this
   must not).

Off by default: most scaffolded projects have no prod host yet, and every
`DEPLOY_*` slot substitutes to `""` when unset — `/deploy` is simply absent
from the render, same invariant DinD/E2E already uphold.

**What this does NOT set up**: an actual prod machine, a registered runner,
or the one-time `deploy` branch / GitHub token. Those are credentialed,
one-off, host-specific steps — see `docs/deploy-pipeline.md` §3 for the
generic checklist, run once per project once a host exists.

### Review loop
| Slot | Value |
|---|---|
| `{{DEVELOP_ROUND_CAP}}` | fixed at `3` (§5 rule 10) — the round cap `/develop`'s Phase 5 hands to the `review-loop` skill |
| `{{FIX_ROUND_CAP}}` | fixed at `2` — the round cap `/fix` and `/refactor` hand to the same skill |
| `{{SOLO_NOTE}}` | non-empty only when `topology` is `solo` (§2) — inserted near the top of every multi-phase command and `workflow.md`, overriding their "spawn `<agent>`" instructions EXCEPT the review phase, which still runs the `review-loop` skill and spawns `reviewer-strict` for every task |
| `{{ZONE_OWNERSHIP_NOTE}}` | `CLAUDE.md`'s zone-table sentence — topology-specific wording (one owner per zone in crew; Alfred owns every zone in solo) |

**Independence is enforced by the reviewer's tool list, not only by its
instructions.** Every persona whose job is "report, never edit" —
`reviewer-granger`, `reviewer-mcgonagall`, `security-holmes`, and solo's
`reviewer-strict` — declares `tools:` in its frontmatter with no `Edit`
(crew reviewers keep `Write`, for their own status/findings files under
`{{TRACKER_ROOT}}`; `reviewer-strict` has neither, since its report goes
straight back to the orchestrator, no tracker files). A "never edit code"
sentence in a persona's prose is a request; the harness refusing the `Edit`
tool call is a fact — the same distinction §2's `reviewer-strict` note
makes for solo specifically.

### Zones
| Slot | Value |
|---|---|
| `{{ZONE_KEY}}` / `{{ZONE_LABEL}}` | this agent's zone — `api` / `HTTP API service` |
| `{{ZONE_KEYS}}` | every zone key, comma-separated |
| `{{ZONE_TABLE}}` | markdown table: key, label, paths, rulebook, verify command, owner |
| `{{OWNED_PATHS}}` | bullet list of this agent's paths, globs allowed |
| `{{OTHER_ZONES}}` | bullet list: other zones' paths + who owns them |
| `{{ZONE_KEY_EXAMPLE}}` | any real zone key, for the YAML example |

### Roster wiring
| Slot | Value |
|---|---|
| `{{LEAD_NAME}}` / `{{LEAD_HANDLE}}` | this dev's lead. **No lead in this project → the architect**: `Charles Xavier` / `xavier` |
| `{{TEAMMATES}}` | bullet list: name — zone — persona file |
| `{{REVIEWERS}}` | who reviews this agent's code |
| `{{TEAM}}` | a lead's developers, as a list |
| `{{LEAD_ZONES}}` / `{{REVIEW_ZONES}}` | zone keys this lead/reviewer covers |
| `{{ARCHITECT_NAME}}` / `{{ARCHITECT_HANDLE}}` | `Charles Xavier` / `xavier` in crew; in solo, `{{ARCHITECT_NAME}}` is `spec["solo_name"]` (default `Alfred`) but `{{ARCHITECT_HANDLE}}`/the slug/the filename stay `alfred` regardless — only the display name is chosen per project |
| `{{SECURITY_NAME}}` / `{{SECURITY_HANDLE}}` | `Sherlock Holmes` / `holmes` |
| `{{RESEARCHER_NAME}}` / `{{RESEARCHER_HANDLE}}` | `Marie Curie` / `curie` |
| `{{PO_NAME}}` / `{{PO_HANDLE}}` | `Taiichi Ohno` / `ohno` |
| `{{DESIGNER_NAME}}` / `{{UI_REVIEWER_NAME}}` | `Leonardo da Vinci` / `Dieter Rams` |
| `{{LEADS_AND_DIRECT_REPORTS}}` | the architect's team, as a list |
| `{{CROSS_CUTTING_ROLES}}` | reviewers, security, research, design — whichever are installed |
| `{{AGENT_REGISTRY}}` | the full registry table for `workflow.md` |

### Policies (authored, see §4)
| Slot | Value |
|---|---|
| `{{PRE_READ}}` | the ordered read pass for this agent, its zone's rulebooks included |
| `{{TEST_POLICY}}` | this project's testing rule. Default: *one e2e scenario per acceptance criterion; unit tests only for pure branchy logic* |
| `{{EXTRA_SELF_CHECK}}` | extra self-check bullets specific to this zone, or empty |
| `{{SECURITY_ZONE_NOTES}}` | security notes specific to this project's surfaces |
| `{{CONTEXT_DOC_POLICY}}` | how orientation docs are maintained. Default: *update the `AGENTS.md` of the area you changed; keep each ≤300 lines; no code in them* |
| `{{PO_PHASE_NOTE}}` | how the PO reaches this project (slash command, tracker dispatch, Warden `Dispatch`) |
| `{{DOC_CONVENTION}}` | the project's doc-comment format — `KDoc`, `docstrings`, `JSDoc`, `godoc` |
| `{{AUTONOMY_LEVEL}}` | how much the fleet may do unattended, from the charter's autonomy answer: `L0 — review each PR` (default), `L1 — batch: open PRs, report once per milestone`, or `L2 — autonomous merge into the integration branch when every gate is green`. Render the level **and** its one-line meaning; never render L2 unless the owner asked for it in so many words |
| `{{GIT_CREDENTIALS}}` | how *this* project authenticates to its remote, as commands. For a container fleet: the `GH_TOKEN` env var, the credential-helper lines that consume it, and where it comes from. For a local-only project: the sentence "this project has no remote; nothing to authenticate" |

### Design (only when the design personas are installed)
| Slot | Value |
|---|---|
| `{{DESIGN_TOOL}}` | e.g. `Figma` |
| `{{DESIGN_TOOL_COMMANDS}}` | the exact tool calls the designer may use |
| `{{DESIGN_SYSTEM_DOCS}}` | the pre-read list for the design system |
| `{{DESIGN_TOOL_RULES}}` | this design system's own hard rules — the ones that cause most rejections |
| `{{DESIGN_FILE_REF}}` | file key / node ids / where the source of truth lives |
| `{{THEME_REQUIREMENT}}` / `{{THEME_LIST}}` | e.g. *every screen exists in Light and Dark* / `Light, Dark` |
| `{{TOKEN_EXCEPTIONS}}` | literal values that are allowed, if any |
| `{{UI_TEXT_LANGUAGE}}` | language of user-facing copy |

---

## 4. Substituted vs. authored

**Substituted** — everything in §3 marked as a value: names, paths, commands,
zone tables, roster wiring. Mechanical. Identical logic in every project.

**Authored** — the per-project rulebooks in `.claude/projects/`, and the
policy slots that depend on them. This is where stack knowledge goes: the Go
error contract, the SwiftUI state rules, the migration policy, the auth model.

The split is the whole point:

- A persona that carries stack knowledge cannot be reused, and cannot be improved
  once eight projects have their own copy of it.
- A rulebook that carries procedure duplicates this kit and rots.

**Personas describe the procedure. Rulebooks describe the project. Never swap
them.** If a rendered persona ends up with a stack-specific rule inline, that rule
belongs in a rulebook and the render was wrong.

---

## 5. Invariants the scaffolder must not break

1. Every rendered file is free of `{{` — verify before writing.
2. No two agents own overlapping paths. Check the zone path sets pairwise.
3. Every zone has a `verify` command that actually runs in the scaffolded repo,
   even if it starts as a stub that exits 0 with "no tests yet".
4. Personas keep their kit filenames (`dev-anderson.md`, not
   `ios-dev-romanoff.md`) so the commands' `subagent_type` references stay valid.
5. The git iron rules in `workflow.md` are copied verbatim: never `main`, never
   `revert`, never auto-resolve a conflict, auto-merge opt-in per task.
6. If the project has no tracker service, remove the `{{TRACKER_CMD}}` command
   blocks — never render a path that does not exist.
7. **Drop what the project does not have.** `workflow.md` is rendered, not
   copied: no PO installed → delete Phase 0 and Phase 8 and the `{{PRODUCT_ROOT}}`
   file conventions; no leads → delete Phase 3 and say specs go straight to the
   developer; no design personas → no design rows in the registry. A phase naming
   an agent that does not exist reads as "someone else handles this" and work
   silently falls through it.
8. **Stamp the kit version.** Write `infra/agentkit/VERSION`'s contents to
   `<project>/.claude/KIT_VERSION` at render time. Without it there is no way to
   tell, six projects later, which of them predate a fix to the kit — the same
   reason `.e2e/profile.yml` carries `kit_version`.
9. Install a command only if every persona it spawns is installed (§2b).
10. **The review-loop controls are load-bearing — never trim them.** The
    round caps (`{{DEVELOP_ROUND_CAP}}`=3 for `/develop`, `{{FIX_ROUND_CAP}}`=2
    for `/fix`/`/refactor`), the ping-pong stop, the `VERDICT:` line and the
    per-round fix verification live in ONE place —
    `infra/agentkit/skills/review-loop/`, rendered into every project's
    `.claude/skills/review-loop/` — not re-described per command. Every
    command with a review phase invokes it instead of reimplementing it;
    that used to be prose duplicated three times (`workflow.md`,
    `develop.md`, `fix.md`/`refactor.md`) and drifted (one copy used
    `must-fix`/`should-fix`, another `critical`/`high`) before it was one
    skill. Without these controls a disagreement between two reviewers
    spawns fleets until someone notices the bill.
11. **Persona filenames are the tracker's join key.** If the project runs a
    Warden, its manifest roster must use the persona filenames verbatim as
    `slug` (`dev-anderson`, `lead-tesla`). Status pushes join on that slug —
    rename one side only and the panel shows an agent that never reports.
12. `{{VERIFY_CMD}}` is zone-scoped and `{{VERIFY_ALL_CMD}}` is project-wide.
   Rendering the same string into both makes a developer "verify" the whole repo
   on every change, which is slow enough that agents start skipping it.
13. **Render with `render.py`, never by hand.** `infra/agentkit/render.py` owns
    the roster algorithm (§2), the command filter (§2b), the slot dictionary
    (§3) and the checks in this section. Hand-rendering re-derives all four
    differently each time, and six projects later nobody can tell which of them
    predate a fix — the reason `KIT_VERSION` exists is to make an upgrade a
    re-run, and it cannot if the render was manual. The model's job is the
    interview, the zones and the rulebooks; it passes those in as `slots`.
14. **Every terminal path in a Warden handler goes through `job.finish()`.**
    `infra/wardenkit/servicer.py` derives the agent's Status FROM the JobResult,
    so the two cannot disagree. Writing a status beside a `return` is how a
    failing run reported `state: done` next to `progress: /develop exited 1` —
    and a record that contradicts itself is read as success, because success is
    the half people act on.
15. **Run the `claude` CLI through `ClaudeRunner`, never with a hand-rolled
    subprocess call.** The kit's runner merges stderr, captures the model's
    answer on success, guards a prompt that starts with `-`, recognises a spent
    subscription, and strips `GH_TOKEN` and `ANTHROPIC_API_KEY`. Every one of
    those was a separate production defect in the first hand-rolled copy.
