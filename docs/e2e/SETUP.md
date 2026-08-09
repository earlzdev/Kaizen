# Adopting `/e2e` in a project

<!--
WHAT: The short adoption path — Warden-backed (dind) projects vs. projects
      with no Warden.
WHY:  docs/e2e/README.md is the design record and its reasoning; this is the
      "do this" version so an owner doesn't have to read the whole thing to
      turn e2e on.
HOW to read it: pick your section by whether the project has a Warden. Both
      end at "Either way".
-->

Full design record: `docs/e2e/README.md`. This is the short version.

## Warden-backed projects (primary path)

If your project was scaffolded by `new-project` / `infra/agentkit/render.py`
with `"e2e": true` in `project.json`, most of this already happened:

1. `dind: true` was auto-set — your Warden container got a sibling `dind`
   engine (`infra/agentkit/MANIFEST.md`, "DinD" section) and `DOCKER_HOST`
   already points at it. The coding agent runs *inside* `warden`, so it can
   run `docker compose` directly — no ssh, no separate host.
2. `docs/e2e/command/e2e.md` was copied to `.claude/commands/e2e.md`.
3. `.e2e/profile.yml` was pre-filled with `env.mode: dind` and
   `boot.up`/`down`/`timeout` (docker compose against
   `deploy/docker-compose.yml`).

What's still on you:

- **Author `deploy/docker-compose.yml`** — your project's own stack. It is not
  part of the kit (`infra/agentkit/MANIFEST.md` §3, DinD) and `boot.up` has
  nothing to run without it. **Two traps**, both because the stack runs on
  `dind`'s own engine, not the host's: a bind mount meant to reach `/repo`
  sees nothing there (it resolves against `dind`'s filesystem), and a
  published port lands on the `dind` container — `boot.ready` polls
  `http://<project>-dind:<port>/...`, never `localhost`.
- **Run `/e2e <thing to prove>`** the first time — it finds `boot.ready`,
  `boot.reset`, `run.*`, `needs` and `boundary` still holding `<placeholder>`
  text in the profile, interviews you for exactly those, and writes the rest.
  Already-filled values (`env.mode`, `boot.up`/`down`/`timeout`) are left
  alone — the interview only asks what's still unanswered.
- **Put test-mode secrets in `.env.example`** — already masked over the real
  `.env` for the Warden (`deploy/warden/docker-compose.yml`), so nothing new
  is needed to keep them out of the agent's `printenv`.

If your project predates this and only has the old profile stub (a comment
saying `/e2e` doesn't exist yet): set `"e2e": true` in `project.json` and
re-render (`python3 infra/agentkit/render.py project.json`) — it forces
`dind: true` itself, copies the method file and pre-fills the profile's
`boot.*` for you. A re-render never overwrites an `.e2e/profile.yml` that
already exists, so this is safe even after you've run the interview once.

## Projects with no Warden

Copy `docs/e2e/command/e2e.md` into `.claude/commands/e2e.md` (and
`~/.claude/commands/` too, for interactive sessions). Run `/e2e <thing to
prove>` — it writes `.e2e/profile.yml` for you via the interview in §1 of the
method file. Pick `env.mode: local` if the agent already has a docker socket
it can reach, or `ci` if this only ever runs in a pipeline.

`env.mode: remote` (a shared box reached over a pinned, forced-command SSH
channel, docs/e2e/README.md §7) is documented there as a **future fallback**
for this case — not built, not wired into any kit today. Don't reach for it
until the box, the script and the forced-command key actually exist.

## Either way

- `kit_version` is stamped in both the method file and the profile. `/e2e`
  warns on a mismatch rather than silently drifting.
- `/e2e` stops interviewing once no `<placeholder>` is left in the profile —
  edit the file by hand after that for profile changes (a new secret, a
  changed boot command). Re-rendering with `render.py` never touches an
  `.e2e/profile.yml` that already exists, so a re-render can't undo your edits.
- The method's rules (red-first proof, no sleeps, quarantine don't delete,
  never touch a visual baseline) are not project-specific and are not
  something a profile can turn off.
