# /e2e — write and run an end-to-end test

kit_version: 1

<!--
This file is the METHOD (docs/e2e/README.md §1): stack-independent, copied
byte-for-byte into every adopting project's `.claude/commands/e2e.md`. It never
carries a project's stack, paths or commands — those live in `.e2e/profile.yml`,
which sits next to this file in the adopting repo. Do not edit a project's copy;
edit `docs/e2e/command/e2e.md` in Kaizen and re-copy (agentkit's render does this
automatically for projects that opt in — see `infra/agentkit/MANIFEST.md`).
-->

`/e2e <what to prove>` — write a scenario for the given behaviour, run it, prove
it actually catches a regression, and report. This is the **behavioural** gate:
it runs the thing, where `/review`-style static passes only read the diff.

---

## 0. Load the profile

Read `.e2e/profile.yml` in this repo.

- **Missing** → run the full interview in §1 below, write the profile, show it
  to the owner, and stop for confirmation before writing any test.
- **Present, with `<angle-bracket>` placeholders left in it** (e.g. a project
  scaffolded with `dind: true` pre-fills `env.mode`/`boot.up`/`down`/`timeout`
  but leaves `boot.ready`, `boot.reset`, `run.*`, `needs` and `boundary` as
  placeholders — see `infra/agentkit/MANIFEST.md`, "E2E") → run §1, but **only
  ask about the sections still containing a placeholder**. Keep every already-
  filled value as is; do not re-ask a question the profile already answers.
- **Present, fully filled in** → check `kit_version` against this file's.
  Mismatch → tell the owner *"this repo's e2e profile is v{X}, the method is
  v{Y}"* and continue (a version drift is a heads-up, not a blocker). This is
  the steady state: the interview never repeats once every placeholder is
  gone.

## 1. The interview (only for whatever is still a placeholder)

Ask everything still unanswered in one message — not drip-fed. A project
scaffolded with dind already has questions 1 and (partly) 2 answered by
`boot.up`/`down`/`timeout`; skip those and ask the rest:

1. **How do I get a running environment?** The exact command(s): `boot.up`.
2. **How do I know it's ready?** A command whose success means ready —
   `boot.ready` — never a fixed sleep. State `boot.timeout` too.
3. **How do I reset or isolate state between runs?** `boot.reset`, or say
   `isolation: per-run` if every run already gets a clean slate.
4. **Which secrets does this need, and where do they come from?** For each:
   a key, `where: host-env` (already present in the environment, never read by
   you) or `where: ask-human` (ask once, never write the value into the repo),
   and why the scenario needs it.
5. **How do I run one test, and where do logs/artifacts land on failure?**
   `run.one` (a single scenario, not the whole suite), `run.report` (machine
   readable — JUnit/JSON, never parsed from coloured terminal output),
   `run.artifacts`, and what to do `on_fail` (e.g. dump container logs).

Write the answers into `.e2e/profile.yml` using `docs/e2e/profile.template.yml`
as the skeleton. Show the filled profile to the owner and wait for confirmation
before writing the first scenario.

**Golden rule for defaults:** use what the repo already has. Adding a new test
runner or a new UI framework "because it's better" is worse than a passing test
in the existing one — see `docs/e2e/README.md` §6 for the fallback table when a
repo genuinely has nothing yet.

## 2. State the scenario before writing it

For the behaviour named in `/e2e <what to prove>`:

- **Given/When/Then.** One scenario per user-visible outcome the feature
  produces — not one per function, not one per code path.
- **The boundary**, copied into the test's own docstring/comment: what's real,
  what's stubbed, what's external (`.e2e/profile.yml`'s `boundary` section is
  the running default — restate it, don't just point at the file).
- **Every input needed, in one batch.** Credentials, seed data, URLs, test
  accounts — ask for all of it now. A scenario half-written because an input
  showed up mid-run is a scenario that will be rewritten.

## 3. Write it

Into `run.write_to` from the profile. One test runner — the repo's own, per the
profile — never bash for the assertions themselves (bash is fine for
`boot.up`/`ready`/`reset`/`down`, which are plumbing, not scenarios: see
README §6, "Bash or Python?"). No sleeps: poll with a timeout. Unique data per
run: never assert on data this run did not create, and never depend on another
test's leftovers — a shared environment is always dirty.

## 4. Run it

```
<boot.up>
poll <boot.ready> until <boot.timeout>
<run.one>
```

## 5. Red-first proof — mandatory, not optional

Green on the first try proves nothing except that the test compiles. Before
reporting success:

1. Break the behaviour under test — revert the change, flip the flag, stub the
   handler, whatever makes the real defect reappear.
2. Run the scenario again. It must fail **on the assertion that matters**, not
   on a setup error or an unrelated crash. If it doesn't go red here, the test
   doesn't test what it claims to.
3. Restore the behaviour. Run again. Green.

This is the one non-negotiable step. A test that has never been watched to fail
is not evidence — it is decoration.

**Red means fix the product, not the assertion.** If a run goes red for a
reason other than the deliberate break in step 1, find out why and fix the
actual cause. Loosening or removing the assertion instead requires stating, in
the report, exactly why the original assertion was wrong — never do it silently
to make a run green.

## 6. Failure, flakiness, baselines

- **A scenario that fails intermittently gets quarantined, never deleted.**
  Mark it (skip + a tracked reason), report it as flaky, and leave the evidence
  for a human to look at. Deleting a flaky test hides the defect it was
  pointing at.
- **Never update a visual baseline.** If this project does pixel-diff visual
  regression, a changed baseline is a human decision — see README §6. Report
  the diff and stop.
- On failure, collect `on_fail`'s artifacts (logs, screenshots, traces) before
  reporting — an agent debugging blind starts guessing at assertions.

## 7. Report

State, plainly:

- the scenario, as Given/When/Then
- the boundary (real / stubbed / external)
- the failure it demonstrably catches (from the red-first proof in §5) — not
  "this should catch X", but "this caught X when I broke it"
- what it does **not** cover
- the artifacts path, if anything went red during this run
- if a scenario was quarantined: which one, why, and the flake evidence

---

## Rules, always

1. Inputs up front, in one batch — never drip-fed mid-run.
2. State the boundary in every scenario.
3. Red-first proof is mandatory. No exceptions for "obviously correct" fixes.
4. Red means fix the product, not loosen the assertion.
5. No sleeps, ever. Poll with a timeout. Unique data per run.
6. Flaky → quarantine, never delete.
7. Never update a visual baseline — that is a human decision.
8. One scenario per user-visible outcome. Pure branchy logic wants a unit test,
   not this command.
