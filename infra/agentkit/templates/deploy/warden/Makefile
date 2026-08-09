# =============================================================================
# {{PROJECT}} Warden — run from THIS directory: cd deploy/warden && make up
# =============================================================================
#   make up            # start the Warden dev-fleet (dev)
#   make up prod       # start it in production mode — REFUSES a template .env
#   make down / logs / ps
#   make login         # ONE TIME: /login in the claude CLI
#   make vendor-kit    # DEVELOPMENT ONLY: refresh the vendored Kaizen kit
#
# This is the fleet that WORKS ON {{PROJECT}} — not the project itself. See
# ../../Makefile for the project's own app stack (`make up` from the root).
# =============================================================================

# --env-file on the VARIABLE, not on each recipe, so a stack added later cannot
# quietly omit it. Resolved relative to CWD (this directory) when `make` runs
# here — so this reads THIS directory's `.env`, never the project root's.
COMPOSE := docker compose --env-file .env -f docker-compose.yml

# The fleet writes into a bind mount; running it as root leaves the owner unable
# to commit in their own checkout. Passed through to the container's `user:`.
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

MODE := $(word 2,$(MAKECMDGOALS))
ifeq ($(MODE),)
MODE := dev
endif
ifeq ($(filter $(MODE),dev prod),)
$(error usage: make up [dev|prod] — got '$(MODE)')
endif

.PHONY: up down logs ps vendor-kit dev prod check-env login

up: check-env
	@# The state volume is created ROOT-owned, and the container runs as your
	@# uid — without this chown the entrypoint dies on its first write to
	@# $$HOME and `restart: unless-stopped` turns that into a silent crash
	@# loop. Idempotent, so it runs on every `make up`, not just the first.
	@docker volume inspect {{PROJECT}}-warden-state >/dev/null 2>&1 || \
	  docker volume create {{PROJECT}}-warden-state >/dev/null
	@docker run --rm -v {{PROJECT}}-warden-state:/state alpine \
	  chown -R $(HOST_UID):$(HOST_GID) /state
	$(COMPOSE) up -d --build
	@echo "Warden is up. Approve it in Kaizen: make approve"
	@echo "First time only: make login   (the claude CLI needs one /login;"
	@echo "the credential persists in the state volume)"

# One-time: log the claude CLI in. The credential lands in
# CLAUDE_CONFIG_DIR=/state/claude, on the state volume, so it survives
# restarts and rebuilds. Until it exists every Directive fails with
# "Not logged in".
login:
	$(COMPOSE) exec warden claude

dev prod:
	@:

check-env:
	@test -f .env || { \
	  if [ "$(MODE)" = "prod" ]; then \
	    echo "ERROR: .env is missing. Production must not boot on generated"; \
	    echo "       defaults — copy .env.example and fill in REAL values."; \
	    exit 1; \
	  else \
	    echo "No .env — generating one from .env.example (development only)."; \
	    cp .env.example .env; \
	  fi; }
# `${VAR:?}` catches UNSET, never PLACEHOLDER, so a fresh production host would
# otherwise boot happily on `replace-me`. Refuse specifically at template values.
	@if [ "$(MODE)" = "prod" ] && grep -qE '=(replace-me|change-me|your-|TODO)' .env; then \
	  echo "ERROR: .env still contains template values:"; \
	  grep -nE '=(replace-me|change-me|your-|TODO)' .env; \
	  echo "Production refuses to start on placeholders. Fill them in."; \
	  exit 1; \
	fi

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

# DEVELOPMENT ONLY — you almost certainly do not need this.
#
# The vendored kit under vendor/ is COMMITTED, so a deploy host has everything
# it needs from a `git clone`. This target only exists to pull a newer Kaizen
# kit into this repo, and it therefore requires a Kaizen checkout.
# The guard is load-bearing: this runs `rm -rf` before it copies, so on a host
# without Kaizen it would destroy the vendored kit and then fail to replace it.
vendor-kit:
	@test -d "$(KAIZEN)/infra/wardenkit" || { \
	  echo "No Kaizen checkout at KAIZEN=$(KAIZEN)."; \
	  echo "If you are on a deploy host, you probably do not need this target:"; \
	  echo "vendor/ is committed and already up to date."; \
	  echo "If you are developing, run: make vendor-kit KAIZEN=/path/to/Kaizen"; \
	  exit 1; }
	rm -rf vendor/infra/wardenkit vendor/infra/proto/gen
	cp -R "$(KAIZEN)/infra/wardenkit" vendor/infra/wardenkit
	cp -R "$(KAIZEN)/infra/proto/gen"  vendor/infra/proto/gen
	@echo "Vendored kit refreshed from $(KAIZEN). Commit the diff."
