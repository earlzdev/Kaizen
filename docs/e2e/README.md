# E2E testing as a pipeline step

<!--
WHAT: The design we settled on for agent-written end-to-end tests: a universal
      method (one instruction file, every project), a per-repo profile (how THIS
      project boots and runs), and secrets that live on the test host. Plus the
      decisions behind each: where the pieces live, what the environment looks
      like, and what tech stack is allowed.
WHY:  the goal is not "add e2e to Kaizen". It is a reusable developer-pipeline
      step — you write "…and also write an e2e test for this feature" in an
      agent's prompt, in ANY of your projects, and it works. Anything that
      hardcodes Kaizen's stack defeats that.
HOW to read it: §1–§2 are the structure (what lives where). §3–§5 are what the
      agent actually does. §6–§7 are the constraints (stack, environment).
      §8 is Kaizen as the first consumer. §9 is what is still open.
      §10 tracks build status — this is still the design record, not a status
      report, but the method/profile/setup pieces it specifies now exist; see
      §10 for what's built and what's still deferred.
-->

Companion to `docs/reference/agent-pipeline/` — that export documents how a fleet
of agents implements a feature; this documents the step that proves the feature
works. `/agentic-loop` is the **static** gate (agents read the diff);
`/e2e` is the **behavioural** gate (agents run the thing).

---

## 1. Three layers, three homes

"The e2e contract" is really three separate things, and bundling them is the
main way this design fails.

| Layer | What it is | Changes | Lives in |
|---|---|---|---|
| **Method** | How to plan, write, run and prove an e2e test. Red-first rule, no-sleeps, boundary honesty. | rarely, globally | one versioned export, synced into every repo |
| **Profile** | How **this** project boots, becomes ready, resets, runs one test, where artifacts land. | with the code | `.e2e/profile.yml` **in the repo** |
| **Secrets / access** | Dev API keys, test accounts, host credentials. | per environment | **on the e2e host only** |

**The rule:** the method is centralized, the profile is co-located, secrets are
neither.

- Centralize the profile (e.g. in a tracker DB) and it goes stale the moment
  someone renames a compose service — the person who renamed it has no reason to
  look there. Co-located config stays true because it breaks in the same PR that
  breaks the app.
- Decentralize the method and you get eight divergent copies of your e2e rules.

---

## 2. Where the method lives (and why not the tracker)

**Decision: the method ships as a project-agnostic export, the same way
`docs/reference/agent-pipeline/` already does.** E2E is a pipeline step, not a
Kaizen feature — it belongs next to `/develop`, `/fix`, `/review`.

Shape (see §10 for what's built vs. still planned):

```
docs/e2e/
  README.md               ← this file (the design record)
  command/e2e.md          ← the METHOD: the universal slash command, byte-identical everywhere [built]
  profile.template.yml    ← the per-repo profile skeleton [built]
  host/e2e-run            ← host-side script the agent is allowed to call [deferred, §9/§10]
  SETUP.md                ← adoption guide for a new project [built]
```

A project adopts it by copying `command/e2e.md` into its own `.claude/commands/`
(and `~/.claude/commands/` for interactive sessions), then answering the
interview once to produce its `.e2e/profile.yml`. Kaizen is just one more
consumer of the export — not a special case.

### Why not store the method in the tracker

The tracker is the wrong home for the **method**: it would couple a generic
developer practice to Kaizen's Hub, so e2e would only work in projects that
registered a Warden. Most projects that need this are not Kaizen projects.

What the tracker **is** good for is **observing** (optional, later): the Hub
already dials every project's Warden and has `Describe`. Let Warden report the
project's profile + last run result, and the dashboard gets a real cross-project
view — which projects have e2e set up, which have no profile, whose last run was
red, whose copy of the method is stale. That is tracker's actual job (visibility
across projects), not storage.

For projects that already import `infra/wardenkit` — the one thing other repos
pull from Kaizen — wardenkit is a fine *carrier* for the method and a validator
for the profile schema. It must not be the only channel, for the reason above.

---

## 3. What the method enforces (stack-independent)

These are the rules that stop agent-written e2e from rotting. They hold in every
language and every stack.

1. **Inputs up front, in one batch.** Before writing anything the agent
   enumerates what it needs — credentials, test accounts, URLs, seed data — and
   asks **once**. Not drip-fed mid-run.
2. **State the boundary** in the test's docstring: what is real, what is stubbed,
   what is external. Every "end-to-end" test stops somewhere; writing down where
   keeps the coverage claim honest.
3. **Red-first proof — mandatory.** Green is not delivery. Break the behaviour
   under test (revert the change, flip the flag, stub the handler), watch it go
   red **on the assertion that matters**, restore, green again. This is the
   difference between a test suite and a compliance ritual: an agent required to
   produce a passing test will otherwise produce a test that *cannot fail*.
4. **Red means fix the product, not the assertion.** Changing an assertion
   requires stating why the original was wrong.
5. **No sleeps, ever** — poll with a timeout. Unique data per run. No cross-test
   ordering. Every test must be re-runnable against a dirty environment, because
   a shared box is always dirty. Never assert on data the test did not create.
6. **Flaky ⇒ quarantine, never delete.** An agent that may delete a failing test
   will.
7. **An agent may never update a visual baseline.** That is a human decision
   (see §6).
8. **Granularity:** one test per *user-visible outcome*, crossing the seams the
   feature touches — not one per function. E2E is needed wherever a feature
   crosses a process, a network, or a persistence boundary. Pure branchy logic
   does not want an e2e test; it wants a unit test.

### Where this attaches in the pipeline

`infra/agentkit/workflow.md` already has the architect
writing acceptance criteria in the TZ **before** anyone codes.

- **Make acceptance criteria literally be e2e scenarios**, in Given/When/Then.
  Then `/e2e` is not inventing a test afterwards to rationalise the diff — it is
  compiling a spec that predates the code. This is the root fix for "the test
  just mirrors the implementation", and it costs nothing since the criteria get
  written anyway.
- Phase 4 §6 currently says "tests per best practices" — vague, which is how you
  get mocked unit tests that assert the implementation back at you. Replace with:
  one e2e scenario per acceptance criterion; unit tests only where a specific
  piece of pure logic is worth pinning.
- Add a **Phase 4.5 (QA/e2e)** owned by its own named persona, so the developer
  who wrote the feature is not the one grading it.

---

## 4. What a run looks like

You write *"…and also write an e2e test for this feature."* The agent then:

1. Reads `.e2e/profile.yml`. **Missing → it interviews you** (the five questions
   in §5), writes the profile, asks you to confirm it. Once per project, forever.
2. States the scenario as Given/When/Then, plus the boundary, plus **every input
   it needs, in one batch**.
3. Writes the scenario into `run.write_to`.
4. Runs `boot.up`, polls `boot.ready` until `boot.timeout`.
5. Runs `run.one` → expects green.
6. **Red-first proof** (rule 3) → red on the right assertion → restore → green.
7. Reports: the scenario, the failure it demonstrably catches, what it does
   **not** cover, and the artifacts path if anything went red.

---

## 5. The profile

The profile answers five questions, and it does so in **commands, not concepts**.
The agent never needs to know whether the project uses compose, k8s or a
simulator — it executes strings and polls a readiness check. That single property
is what lets one instruction file serve a Kotlin app and a Python backend.

1. How do I get a running environment?
2. How do I know it is ready? (a signal, never a sleep)
3. How do I reset or isolate state between runs?
4. Which secrets does this need, and where do they come from?
5. How do I run one test, and where do logs/artifacts land on failure?

```yaml
# .e2e/profile.yml — how THIS project does e2e.
kit_version: 1

env:
  mode: remote            # local | remote | ci
  host: e2e-box           # ssh alias; forced-command pinned (see §7)
  isolation: per-run      # per-run | shared

boot:
  up:      "make e2e-up"                  # must be idempotent
  ready:   "curl -fsS $E2E_BASE/health"   # polled with timeout — never slept on
  timeout: 120
  reset:   "make e2e-reset"               # or: none, when isolation is per-run
  down:    "make e2e-down"

run:
  all:       "pytest tests/e2e -q"
  one:       "pytest {file}::{name} -q"
  write_to:  "tests/e2e/scenarios/"
  report:    "junit:.e2e/artifacts/{run}/results.xml"   # machine-readable, not stdout
  artifacts: ".e2e/artifacts/{run}/"
  on_fail:   ["docker compose -p $E2E_PROJECT logs --no-color", "screenshot"]

needs:                                     # asked once; never written into the repo
  - key:   STRIPE_DEV_KEY
    where: host-env                        # host-env | ask-human
    why:   "checkout scenarios hit Stripe test mode"

boundary:                                  # the honesty section, copied into each test
  real:     [postgres, api, worker]
  stubbed:  [llm-api, telegram]
  external: [stripe-test]
```

Swap three lines and it is a different stack — the method file stays identical:

```yaml
# mobile repo                              # web repo
boot.ready:   "adb shell getprop sys.boot" boot.ready:   "curl -fsS localhost:3000/healthz"
run.one:      "maestro test {file}"        run.one:      "npx playwright test {file} -g '{name}'"
run.write_to: "e2e/flows/"                 run.write_to: "e2e/specs/"
```

Two loose ends to design in from the start:

- **`kit_version` stamped in both files**, so `/e2e` can warn *"this repo's method
  is v1, the kit is v3"*.
- **`needs` resolution:** `where: host-env` means the agent never sees the value.
  A missing key fails the run with *"`STRIPE_DEV_KEY` is not set on the e2e
  host"* and the agent asks you to put it there. That keeps the "never read
  `.env`" discipline working in every project, enforced by design rather than by
  trust.

---

## 6. Tech stack policy

**No global stack. Pin one per repo, in the profile.** Mandate pytest and the
method dies in a Kotlin repo; mandate nothing and an agent invents a new runner
per feature.

The method requires only **three properties** of whatever the project already
uses:

1. **A single test is addressable** — run *one* scenario, not the suite,
   otherwise the fix-loop costs minutes per iteration.
2. **The result is machine-readable** — JUnit XML / JSON, not prose. An agent
   parsing coloured terminal output will eventually misread red as green.
3. **Failure leaves artifacts** — logs, and for UI a screenshot or trace.
   Without them the agent debugs blind and starts guessing at assertions.

**Golden rule: use what the repo already uses.** An agent that adds Playwright to
a repo that already has Cypress has made things worse even if the test passes.

### Defaults for the interview (only when a repo has nothing yet)

| Surface | Default | Why |
|---|---|---|
| HTTP / backend service | the repo's native runner (pytest+httpx, Go `testing`, vitest+supertest) | zero new deps; fixtures and timeouts for free |
| Web UI | Playwright | the trace viewer is the best agent-debugging artifact available — it replays the failing run |
| Mobile | Maestro (YAML flows) | an agent writes them reliably; use XCUITest/Espresso only if the repo already has them |
| CLI tool | native runner driving subprocesses | `bats` only if the repo is already bash-native |

### Screenshots — three uses, three verdicts

- **As evidence** (captured on failure, attached to the report): **always yes.**
  No flakiness cost, no assertions, large debugging value. Same for traces/video.
- **As assertion (pixel-diff visual regression): no**, not for agent-written
  tests. Baselines break on fonts, OS, antialiasing, a scrollbar — and the
  agent's obvious "fix" is to re-record the baseline, which silently deletes the
  test's value while turning it green. If visual regression is ever wanted, rule
  7 of §3 is absolute: **an agent may never update a baseline.**
- **VLM judging a screenshot** ("does this look like an error banner?"):
  nondeterministic — fine as a soft signal in the report, never as the pass/fail
  gate.

For UI, assert on **semantics** (role, visible text, state, URL) and let the
screenshot be the artifact, not the oracle.

### Bash or Python?

Split by role:

- **Bash for the plumbing** — `boot.up`, `ready`, `reset`, `down`. These are
  shell one-liners already; that is exactly what the profile's command strings
  are.
- **A real test runner for the scenarios — never bash.** Scenarios need
  assertions, poll-with-timeout, fixtures, per-test selection and structured
  output. Bash has none of those, so bash suites converge on
  `sleep 5 && curl | grep` — precisely the flakiness this method exists to
  prevent.

---

## 7. The e2e environment

| Shape | Strength | Fails when |
|---|---|---|
| **Local ephemeral** (agent boots the stack in its workspace) | fastest loop, hermetic, no secrets leave the machine | the stack is heavy; you need a public URL for webhooks; the agent itself runs in a container with no docker socket |
| **Shared remote dev box** (agent SSHes in) | the only place a real dev API key can live without entering the agent's context | shared mutable state — test #2 fails on test #1's leftovers; an agent with a shell on the credential box is a wide blast radius |
| **Remote box, per-run namespaced stack** | keeps the box's advantages, removes the collisions | needs a small amount of host-side tooling |
| **CI runner** | cleanest secret story, reproducible | 3–5 min round trip per iteration makes the agent's fix-loop agony; it debugs by scraping logs |

**Decision: remote box with a per-run namespaced stack, reached through one fixed
command — not a shell.** Local ephemeral stays supported as the fallback for
repos light enough to boot in place; `env.mode` in the profile says which a given
project uses, so the same `/e2e` handles both.

Concretely, for the remote mode:

- One script on the host, `e2e-run <repo> <ref> <selector>`, pinned to the
  agent's key with an SSH forced command
  (`command="…" ssh-ed25519 …` in `authorized_keys`). The agent can start a run
  and read its output; it cannot read env files, touch other projects, or leave
  anything behind.
- **Every run gets its own namespace** (`COMPOSE_PROJECT_NAME=e2e-<branch>-<run>`,
  own volumes), torn down after. This is what makes one box safe for many
  projects and many concurrent agents.
- **Secrets live on the box**, in a file the agent never reads. Missing key ⇒ the
  run fails naming the key ⇒ the agent asks you to put it there. The value never
  enters the transcript.
- **Code arrives by git ref, not by copying a working tree** — reproducible, and
  "which code failed" is always answerable. A `--dirty` sync escape hatch for the
  tight fix-loop is allowed but must be explicit, so nobody reports a green run
  for uncommitted code.

**Known constraint:** the remote agent path in
`docs/reference/agent-pipeline/runtime/agent/agent_runner.py` runs `claude -p`
*inside* a container. Such an agent cannot boot a local stack without a mounted
docker socket — which is another reason the remote-host mode is the default.

---

## 8. Kaizen as the first consumer

Notes specific to this repo, kept here so the method file stays project-agnostic.

**Why e2e over unit tests here.** Kaizen is glue: Кая → Brain → gRPC module →
Postgres → sweeper → delivery push back to the agent. Nearly every bug that has
actually bitten lives *between* two services (the tracker-v2 invariants — lease
expiry, capacity, `task_id` identity, panel escaping — are contract bugs, not
algorithm bugs). A unit test with a mocked Brain client cannot see those, and an
agent writing one mocks exactly what it just wrote. Keep unit tests only where
logic is pure and branchy (lease/capacity arithmetic, sweeper time math, the
style gate, backup catch-up, panel escaping): **e2e is the acceptance gate, units
are a debugging aid.**

**Test tiers.**

| Tier | Drives from | Deterministic | Use |
|---|---|---|---|
| **flow** (default) | fake Telegram transport → real Кая → real Brain → real modules → real Postgres, **stubbed LLM** | yes | the gate |
| **contract** | straight at Brain's front door (MCP/HTTP) as if an agent | yes | module/tool scenarios where Кая adds nothing |
| **live** | real Telegram + real Claude | no | manual smoke before a release; never an automatic gate |

**Contract needs neither an LLM nor Кая's enrollment flow.** Brain never calls
an LLM itself (CLAUDE.md's service-isolation rule: only `agents/core` does),
and a contract-tier test authenticates the same way any of Brain's own admin
tooling does — `POST /admin/agents` with `BRAIN_ADMIN_TOKEN` mints an agent
token on the spot. No fake-LLM container is needed to build and run contract
tier. (Flow tier, below, turns out not to need enrollment either — for a
different reason than "not built yet".)

**Seams that already exist for `contract` (no product code needed).**

- **Auth:** `BRAIN_ADMIN_TOKEN` (from `.env.e2e.generated`) mints a fresh agent
  token via the admin API — no enrollment, no approval, no interactive step.
- **Schema:** every service applies its Alembic migrations at boot
  (`infra.migrations.runner.upgrade`, called from `brain/main.py`,
  `agents/kaya/main.py`, `modules/mentor/main.py`, `agents/kuzya/agent.py`) —
  `modules/tracker/main.py` is the one exception, still on
  `metadata.create_all` because it owns exactly its own tables. A fresh e2e
  stack from zero is still one `up`; it just runs real migrations, not
  `create_all`, to get there.
- **Precedent:** `modules/tracker/example/dummy-project/` speaks the whole
  Warden contract, but it's a **manual** harness — its own README, no
  Makefile target, no automated runner. Read as a worked example of talking
  to Brain/tracker from outside, not something `contract` tier invokes.
- **Runner:** `pytest.ini` already has `testpaths=tests`, `pythonpath=.`,
  `asyncio_mode=auto`, and `tests/integration/conftest.py`'s scratch-DB
  fixture (Brain's models only, one throwaway Postgres DB per test, no
  services started) is a starting point, not a running harness — contract
  tier needs the compose overlay itself, not just a DB fixture.

**`flow` is built (§10 item 6 has the full account) — the seams below turned
out different from what an earlier draft of this doc assumed, in both
directions:**
- **LLM:** true as originally stated — `agents/core/llm.py` builds
  `AsyncAnthropic(api_key=…)` with no explicit `base_url`, so the SDK's own
  `ANTHROPIC_BASE_URL` env var handling works unmodified. `infra/e2e/fake_llm.py`
  replays scripted turns behind it.
  (`infra/`, not `tests/`, because `tests/`+`docs/` are excluded from the
  Docker build context — see `.dockerignore` — and this has to run *inside* a
  container.)
- **Telegram:** the "no transport abstraction exists, this needs a live
  product-code refactor" assessment an earlier draft made here was WRONG — a
  live spike (before writing any code) found `agents/kaya/connector.py::build_router()`
  never constructs a `Bot` at all; it only touches `message.bot.send_*`, and
  aiogram attaches whatever `Bot` a `Dispatcher.feed_update()` call is given.
  So the fake transport is a `BaseSession` subclass swapped in from OUTSIDE
  (`tests/e2e/fakes/fake_telegram.py::FakeSession`) — zero changes to
  `connector.py` or `delivery.py`, confirmed by `git diff` showing nothing
  under `agents/` for that round.
- **Enrollment auto-approve turned out unnecessary, not just deferrable.**
  Flow tier does not run a `kaya` container and therefore never exercises the
  interactive `make approve` path at all — the "real Кая" it drives is
  `agents.core.Agent` + the real `connector.py`, constructed directly by the
  TEST PROCESS (`tests/e2e/flow/conftest.py`), authenticated with an
  admin-minted Brain token exactly like `contract` tier's `agent_token`
  fixture. `agents/core/mcp_client.py::BrainMCPClient` takes `(base_url,
  token)` and does not care how the token was obtained — so this isn't a
  shortcut around enrollment, it's a real code path (`Agent` + `BrainMCPClient`)
  that genuinely never touches enrollment in production either. What this
  means flow tier does NOT cover: enrollment/approval itself, `agents/kaya/main.py`'s
  full boot wiring (locale loading, `SeenUpdatesMiddleware` dedup,
  `DefaultBotProperties`, the delivery receiver), and the real soul/cliché
  files (the harness uses a one-line stub soul, not `locales/*/soul.md`).

**Environment:** a `deploy/docker-compose.e2e.yml` overlay run under its own
compose project with `.env.e2e.generated` generated from `.env.example` (fake
secrets) — the real `.env` is never involved, which is also what makes the
stack safe for an agent to boot unattended. Keep the stack **warm** between
tests (namespace or truncate per scenario); a full up/down per test costs
minutes inside a fix-loop.

**Secrets split (built — `docs/e2e/host/e2e_gen_secrets.py`, a project-agnostic
kit export like `command/e2e.md`):** two files, not one, because flow/contract
and live need different trust levels. Every project defines its own keys —
the script only understands one sentinel, so it never needs to know a
project's service names.
- `.env.e2e.generated.example` (committed) lists every flow/contract-tier key;
  a value of `GENERATE` gets a fresh `secrets.token_urlsafe` on every `make
  e2e-secrets`, anything else is copied through as-is (fixed non-secret test
  config). Output is `.env.e2e.generated` (gitignored) — the agent may read it
  freely, nothing in it is real.
- `.env.e2e.live.secrets` — real, spend-capped credentials (for Kaizen:
  `ANTHROPIC_API_KEY` + a throwaway `TELEGRAM_BOT_TOKEN`), needed only by the
  `live` tier (never an automatic gate). Hand-provisioned once from
  `.env.e2e.live.secrets.example`, gitignored, and covered by the same
  never-read-directly rule as `.env` (see root `CLAUDE.md`). `make
  e2e-secrets-live` fails loudly naming the missing file or key instead of the
  stack silently booting without it.

**Kit export:** `infra/agentkit/render.py`'s `write_e2e_files()` copies
`e2e_gen_secrets.py` into every e2e-enabled project's `.e2e/` (re-copied every
render, like `e2e.md`) and, on first render only, writes starter
`.env.e2e.generated.example`/`.env.e2e.live.secrets.example` skeletons for the
owner to fill in with their own project's keys — same pattern as
`.e2e/profile.yml`'s pre-fill. `Makefile`/`.gitignore`/`CLAUDE.md` gain the
matching targets, ignore lines and never-read rule only when `"e2e": true`
(see `infra/agentkit/MANIFEST.md` "E2E").

**Screenshots are near-irrelevant here** — the only HTML surfaces are Brain's
`/admin/panel` and the tracker dashboard. At most one shallow smoke each, and
only if a panel bug ever actually bites.

---

## 9. Open questions

- Does the export live under `docs/e2e/` permanently, or move into
  `infra/agentkit/commands/` once it is proven, so a project
  adopting the pipeline gets it in the same copy?
- Distribution mechanics: copy-in, git submodule, or a `make sync-agent-kit`
  fetch? (Affects how `kit_version` drift is detected and repaired.)
- Which host is the e2e box, and does one box serve all projects (recommended) or
  one per project?
- Does the tracker observation layer (§2) get built at all, and if so does the
  profile travel over Warden's `Describe` or as a plain file read?
- For Kaizen: is the fake-LLM container per-repo or a shared piece of the kit?
  (Scripted-turn replay is generic; the scripts are not.)

---

## 10. Build status

1. ~~`docs/e2e/command/e2e.md`~~ — **built**: the method, as a slash command.
2. ~~`docs/e2e/profile.template.yml`~~ — **built**: annotated skeleton + the
   five interview questions.
3. `docs/e2e/host/e2e-run` — **deferred**. Warden-backed projects use the dind
   path instead (`infra/agentkit/MANIFEST.md`, "E2E" — the coding agent
   already runs inside `warden`, which already gets `DOCKER_HOST` wired to a
   sibling docker engine, so §7's remote-box/SSH design was never needed
   there). This item is only for a project with **no** Warden that still wants
   `env.mode: remote` — write it when one actually needs it.
4. ~~`docs/e2e/SETUP.md`~~ — **built**: adopting this in a new project,
   split by whether the project has a Warden.

Also built: `infra/agentkit/render.py` wires `"e2e": true` in `project.json`
to auto-enable `dind`, copy the method file and pre-fill the profile's
`boot.up`/`down`/`timeout` for a new Warden project — see
`infra/agentkit/MANIFEST.md`, "E2E".

5. ~~Kaizen's own `.e2e/profile.yml` + `deploy/docker-compose.e2e.yml`~~ —
   **built, `contract` tier only.** Own compose project (`kaizen-e2e`):
   postgres/brain/tracker/mentor/tools, `kaya`/`backup`/`kuzya` removed
   outright (`!reset null`). `make e2e-up`/`e2e-down`. Proven with a real
   cold-start (`down -v` → `up`) and a real warm restart (`down` → `up`,
   volume intact) — the warm-restart run caught a real bug: `POSTGRES_PASSWORD`
   was marked `GENERATE` in `.env.e2e.generated.example`, which regenerates
   every run, while the volume it authenticates against survives across runs
   — every service's DB connection broke on the second `up`. Fixed by making
   it a fixed value instead (both in Kaizen's own example file and in the
   warning added to the kit's starter skeleton in `render.py`, so a new
   project doesn't repeat it). First scenario: `tests/e2e/test_memory_roundtrip.py`
   — mints an agent via `BRAIN_ADMIN_TOKEN`, no enrollment involved, calls
   `remember_fact`/`recall_memory` over real MCP JSON-RPC, asserts on the
   tool's own response (real pgvector round-trip, no LLM anywhere in this
   tier). Not built: any report/artifact format beyond pytest's own stdout
   (see `.e2e/profile.yml`'s `run.report`/`run.artifacts`).
6. ~~`flow` tier~~ — **built.** A live spike (before writing any code) proved
   the earlier "aggregate transport abstraction refactor" fear wrong:
   `agents/kaya/connector.py::build_router()` never constructs a `Bot` at
   all — it only touches `message.bot.send_*`, and aiogram attaches whatever
   `Bot` a `Dispatcher.feed_update()` call is given. So the fake Telegram
   transport is a `BaseSession` subclass
   (`tests/e2e/fakes/fake_telegram.py::FakeSession`) swapped in from OUTSIDE,
   zero changes to `agents/kaya/connector.py` or `delivery.py` — confirmed by
   `git diff` showing nothing under `agents/` for this round. Caught one real
   bug building it: `FakeSession` as a `@dataclass` subclassing `BaseSession`
   silently skipped `BaseSession.__init__` (dataclass generates its own),
   so `self.middleware` never got set and the very first `send_chat_action`
   crashed — fixed with a plain `__init__` calling `super().__init__()`.
   Also caught: `make e2e-down` used the contract-tier-only compose files, so
   it never knew flow tier's `fake-llm` service existed and left it running
   (network stuck on "still in use") — fixed by always using the superset of
   compose files for `down` (harmless no-op for a service that was never
   created).

   The `flow` tier does NOT use a `kaya` container — the "real Кая" it
   drives is the real `agents.core.Agent` + the real `connector.py`,
   constructed by the TEST PROCESS itself
   (`tests/e2e/flow/conftest.py::kaya_agent`), talking to the real
   (published) Brain/Postgres and a scripted `infra/e2e/fake_llm.py`
   (`ANTHROPIC_BASE_URL`, no SDK changes). This also means the auto-approve
   enrollment blocker this doc originally predicted never had to be built:
   an admin-minted Brain token (same mechanism `contract` tier already uses)
   works exactly as well as a self-enrolled one for `agents.core.Agent` —
   it doesn't care how it got its token, only that it has one. `infra/e2e/`
   (not `tests/e2e/`) is where the fake-LLM server had to live: `tests/`
   and `docs/` are both excluded from the Docker build context
   (`.dockerignore`), so anything that needs to run INSIDE a container
   can't live there — `infra/` isn't excluded and is already how every
   other service's shared code reaches the image.

   `deploy/docker-compose.e2e-flow.yml` layers on top of the contract-tier
   overlay: adds `fake-llm` (reuses the same shared Dockerfile/image,
   `command: python -m infra.e2e.fake_llm`) and publishes postgres
   (`127.0.0.1:15432`) — the flow tier's `DbHistory` needs to reach the
   `kaya` logical database directly from the host test process, since no
   `kaya` container exists to reach it from the inside. `make e2e-up-flow`
   also creates+migrates that database (`agents.kaya.main.ensure_database`
   + `infra.migrations upgrade kaya`, run via `exec -T brain` — brain's
   image has `agents.kaya`'s code even though it never runs it) — nothing
   else does, since the kaya container that normally does this at boot
   never runs. Proven with a full cold start (`down -v` → `up-flow`) and a
   warm restart, plus contract tier's 2 tests and flow tier's 1 running
   together with no interference.

   First scenario: `tests/e2e/flow/test_conversation.py` — a synthetic
   Telegram message drives the real dispatch → real tool loop → real
   Brain `remember_fact` call (asserted via the fake LLM's own `/_calls`
   log, proving the REAL tool schema reached the fake model, not a stub)
   → a scripted final reply, asserted arriving back over the fake Telegram
   session. Never asserts on model-generated text, only on what was
   scripted.

7. **Still not built, deferred:** `live` tier (real Telegram + real Claude,
   manual smoke only, never an automatic gate — see `.env.e2e.live.secrets`
   from earlier in this section). No blockers found; simply out of scope for
   an automated tier.
