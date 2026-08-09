# =============================================================================
# {{PROJECT}} — this project's OWN app stack (run from the repo root)
# =============================================================================
#   make up            # start the app (dev)
#   make up prod       # start it in production mode
#   make down / logs / ps
#   make test          # every zone's verify command
#
# This is the app itself, not the fleet that builds it — see
# deploy/warden/Makefile (`cd deploy/warden && make up`) for the Warden
# dev-fleet.
#
# deploy/docker-compose.yml (+ .dev.yml / .prod.yml overlays) is authored per
# project, not part of the Kaizen kit — `make up` refuses with a clear
# message until it exists.
# =============================================================================

DEV  := docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml
PROD := docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml

# `make <target> [dev|prod]` — the word after the target selects the stack;
# default dev. The MODE word is also a (no-op) target below so make doesn't
# complain.
MODE := $(word 2,$(MAKECMDGOALS))
ifeq ($(MODE),)
MODE := dev
endif
ifeq ($(filter $(MODE),dev prod),)
$(error usage: make up [dev|prod] — got '$(MODE)')
endif
COMPOSE := $(if $(filter prod,$(MODE)),$(PROD),$(DEV))

.PHONY: up down logs ps test check-env dev prod{{SECRETS_PHONY}}

up: check-env
	@echo "Starting the $(MODE) app stack…"
	$(COMPOSE) up -d --build

# Argument word for `make up dev|prod` — a no-op on its own position.
dev prod:
	@:

check-env:
	@test -f deploy/docker-compose.yml || { \
	  echo "No deploy/docker-compose.yml yet — this project's own app stack is"; \
	  echo "authored per-project, not scaffolded by the Kaizen kit. Write it,"; \
	  echo "plus deploy/docker-compose.dev.yml / deploy/docker-compose.prod.yml"; \
	  echo "overlays for a dev/prod split, then re-run 'make up'."; \
	  exit 1; }
	@test -f deploy/docker-compose.$(MODE).yml || { \
	  echo "No deploy/docker-compose.$(MODE).yml yet — deploy/docker-compose.yml"; \
	  echo "exists, but its $(MODE) overlay doesn't. Add one (ports/log-rotation"; \
	  echo "split, e.g. Kaizen's own deploy/docker-compose.$(MODE).yml), then re-run."; \
	  exit 1; }
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

test:
	{{VERIFY_ALL_CMD}}
{{SECRETS_MAKE_TARGETS}}
