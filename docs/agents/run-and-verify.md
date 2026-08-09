# How does the project run, and how do I prove my change works?

<!--
WHAT: The runtime contract — everything runs in docker, there is no local venv —
      and what counts as proof that a change works.
WHY:  agents default to "I ran the unit test, it passes" or to creating a local
      virtualenv. Neither is how these projects run, so neither is evidence.
STATUS: STUB — outline only.
-->

## To fill in

- **The runtime is docker.** No local venv, no `pip install` on the host. The
  only supported way to run anything is the compose stack.
- **The standard verbs** — `make up [dev|prod]`, `make down`, `make logs`,
  `make ps`, and what each does.
- **Dev vs prod overlays** — base compose is closed (no published ports); the dev
  overlay is the only place ports open, bound to loopback.
- **How to know the stack is healthy** — healthchecks, what a good `make ps`
  looks like, which log lines mean "booted".
- **What counts as proof**, in ascending order of strength:
  1. it compiles / imports — not proof
  2. a unit test passes — weak (may only assert the implementation back at you)
  3. the stack boots healthy with the change — necessary, not sufficient
  4. an e2e scenario passes **and is proven able to fail** — proof.
     See [e2e-setup.md](e2e-setup.md).
- **What to paste in the report**: the actual command and its actual output.
  Never "should work now".

## Kaizen-specific

- `make up` also runs the interactive agent approval step
  (`infra/scripts/approve.sh`) — an unattended agent will hang there. Noted as a
  blocker in [`docs/e2e/README.md`](../e2e/README.md) §8.
- Compose must run with `--env-file .env` from the repo root.
