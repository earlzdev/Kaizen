# Where do secrets live and what may I read?

<!--
WHAT: The rule for credentials — which files an agent may open, which it may
      never open, and how a missing key is supposed to surface.
WHY:  this is the one mistake that cannot be undone by a follow-up commit: a real
      secret printed into a transcript is leaked. The rule has to be structural
      ("the agent is never given the value"), not a matter of care.
-->

## The rules

- **Never read or print `.env`.** Use `.env.example` for reference and for edits.
  This applies to every project, not just Kaizen.
- **`.env.example` is the contract**: every key the app needs appears there with
  a placeholder value and a one-line comment. Adding a key to the app means
  adding it to `.env.example` in the same change.
- **Where real values live** per environment: owner's machine (`.env`), the e2e
  host (host env file the agent cannot read), production host.
- **How a missing key surfaces** — the run fails *naming the key*, the agent asks
  the owner to set it there. The agent never receives the value. See
  [`docs/e2e/README.md`](../e2e/README.md) §5 (`needs:` / `where: host-env`).
- **Tokens that are issued, not configured** — the enrollment model: agents are
  approved once and store their own token; no agent token belongs in `.env`.
- **What to do if a secret is exposed anyway**: stop, tell the owner, do not try
  to scrub git history unasked.

## Structure beats discipline

The rule above is a request. These are facts, and they are what a scaffolded
project actually ships with:

- **The fleet's container sees a fake `.env`.** Its compose file mounts
  `.env.example` over `/repo/.env` **and** `/repo/deploy/warden/.env` read-only
  (the app's own env and the Warden's own — two files since
  `infra/agentkit`'s Makefile split), so a `grep` across the tree or a `cat` in
  a debugging step finds committed placeholders. The real values reach the
  Warden through `environment:`, resolved on the host by `--env-file`.
- **`GH_TOKEN` is not in the fleet's environment.** The entrypoint stores it in
  `gh`'s own config (`env -u GH_TOKEN gh auth login --with-token` — the `env -u`
  is required, gh refuses while the variable is set) and writes
  `~/.git-credentials`, then `exec env -u GH_TOKEN` hands off without it.
  `printenv GH_TOKEN` returns nothing; `gh` and `git push` still work.
- **Known residue, stated rather than implied:** `~/.git-credentials` inside the
  container is plaintext, and an agent with Bash can read it. Closing that needs
  a credential helper that does not store plaintext. Until then, the token is
  repo-scoped and fine-grained precisely because it is not fully contained.

## Two traps in how the values arrive

Both cost an evening the first time, and neither produces an error message.

- **Every compose invocation needs `--env-file`, including the ones added
  later.** Without it, Compose falls back to a `.env` in the *project*
  directory (the compose file's own directory by default) — never the repo
  root just because that's where you happen to be standing. `--env-file`
  itself is resolved against the *current working directory*, which is why
  each stack's Makefile always runs from that stack's own directory (`deploy/
  warden/` for the Warden, the repo root for the app): run compose from the
  wrong directory and `--env-file .env` silently reads the wrong stack's
  `.env` — or none — and every `${VAR:-}` becomes empty. Put the flag on the
  Makefile *variable*, never in each recipe.
- **Compose substitutes from the shell environment too, and the shell wins over
  the file.** A variable exported in the operator's shell (or left over from a
  previous `export`) silently overrides the value sitting correctly in `.env`.
  If a container is behaving as though `.env` says something it does not, check
  `printenv` on the host before editing the file again.

## Believing a secret arrived

"It's in the file" and "the process has it" are different claims. Prove the
second one with a live check, never by reading the value:

```bash
docker exec <warden> gh api repos/<owner>/<repo> --jq '{push: .permissions.push}'
docker exec <warden> sh -c 'cd /repo && git push --dry-run origin HEAD:refs/heads/probe'
```

A container that boots and says "local-only" while `.env` has a token is the
`--env-file` bug. A container restarting every few seconds is a fatal error in
the entrypoint — which is why the `gh` login there is deliberately non-fatal.

## Kaizen-specific

- `BRAIN_ADMIN_TOKEN` (admin panel/API), `DELIVERY_TOKEN` (Brain→agent pushes),
  `MODULE_EVENT_TOKEN` (tracker→Brain events), per-project tracker tokens.
  Agents pair via `make approve`, not via `.env`.
- **`MODULE_EVENT_TOKEN` is required, not optional.** The tracker refuses to boot
  without it and Brain prints a banner: unset, every report and question is
  dropped silently. Both sides must hold the *same* value — when they hold two
  different ones the failure is a runtime 401 that no boot check can see, so
  prove it with `make notify-selftest`.
- Checks reject **placeholder** values, not just empty ones (`infra/config_checks.py`):
  `${VAR:?}` catches *unset*, and a `.env` copied from the template is neither
  unset nor valid.
