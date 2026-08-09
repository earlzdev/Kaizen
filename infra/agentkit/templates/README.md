# {{PROJECT}}

{{PURPOSE}}

## Run it

Two independent stacks, each with its own `.env` and its own `make up` —
the Warden dev-fleet that BUILDS this project, and the project's own app.

**The Warden dev-fleet** (`deploy/warden/`):

```bash
cd deploy/warden
cp .env.example .env      # then fill in the real values
make up                   # start the fleet (chowns the state volume first —
                          # it is created root-owned, the container is not root)
make login                # ONE TIME: /login in the claude CLI. The credential
                          # persists in the state volume; until it exists every
                          # Directive fails with "Not logged in".
make logs                 # watch it
```

Then approve the project once, from Kaizen: `make approve` (or the panel's Fleet
view, or ask Кая).

`make up prod` (run from `deploy/warden/`) refuses to start while `.env` still
contains template values — `${VAR:?}` catches *unset*, not *placeholder*, and
a production host booting on `replace-me` is worse than one that will not boot.

**The project's own app** (repo root):

```bash
cp .env.example .env      # then fill in the real values
make up                   # start the app (dev)
make up prod              # ... or production
make logs                 # watch it
```

`deploy/docker-compose.yml` (+ `.dev.yml`/`.prod.yml` overlays) is authored
per-project, not part of the scaffold — `make up` refuses with a clear message
until it exists.

## Verify it

```bash
make test        # every zone
```

Per-zone commands are in the table in `CLAUDE.md`.

## How the fleet is authenticated

`GH_TOKEN` from `deploy/warden/.env` reaches the container through compose, and
the entrypoint moves it into `gh`'s own config before dropping it from the
environment — so no agent can `printenv` it. `git push` works from the
credential store.

Known residue, stated rather than implied: `~/.git-credentials` inside the
container is plaintext, and an agent with Bash can read it. Closing that needs a
credential helper that does not store plaintext.

## Updating the Warden

```bash
git pull && docker restart {{PROJECT}}-warden
```

It runs from the mount, not from the image, so a fix does not need a rebuild.
The trade: start the container mid-checkout and it runs a half-written file.
The image keeps its own copy as a fallback — see `deploy/warden/docker-compose.yml`.

Restarting still interrupts whatever Directive is running; the Hub requeues it.

## Concurrency

`MAX_CONCURRENT=1`, because the fleet works in this checkout and two Directives
would interleave their diffs. Raising it needs a git worktree per Directive.
