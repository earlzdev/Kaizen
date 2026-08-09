.PHONY: help up down agent logs build rebuild shell login import-auth fix-auth-perms ps

# docker compose with .env from repo root
DC := docker compose --env-file ../.env

# Default target
help:
	@echo ""
	@echo "  YourProject Dev — Docker commands"
	@echo ""
	@echo "  make up            Start tracker + bot + agent-runner (background)"
	@echo "  make down          Stop everything"
	@echo "  make agent         Open interactive Claude session"
	@echo "  make logs          Follow all container logs"
	@echo "  make logs-runner   Follow agent-runner logs"
	@echo "  make build         Build all images"
	@echo "  make rebuild       Force rebuild all images"
	@echo "  make shell         Open bash in agent container (no Claude)"
	@echo "  make ps            Show container status"
	@echo ""
	@echo "  First-time setup:"
	@echo "  make login         Authenticate Claude (Max plan OAuth) — run once per machine"
	@echo "  make import-auth   Copy local ~/.claude auth into volume (local dev shortcut)"
	@echo ""
	@echo "  make reset-workspace  Delete workspace volume (re-clones on next start)"
	@echo ""
	@echo "  Troubleshooting:"
	@echo "  make fix-auth-perms  Fix permissions on claude-auth volume (run if 401 errors)"
	@echo ""

# Start the full stack in background
up:
	@touch ~/.claude.json  # ensure bind-mount source exists (remote servers won't have it)
	$(DC) up tracker bot agent-runner -d
	@echo ""
	@echo "  Tracker:  http://localhost:3333"
	@echo "  Bot:      running"
	@echo "  Runner:   running (trigger-based)"
	@echo ""

# Stop everything
down:
	$(DC) down

# Open an interactive Claude session (for manual work / debugging)
agent:
	$(DC) run --rm agent claude --dangerously-skip-permissions

# Open bash in agent container (for debugging)
shell:
	$(DC) run --rm agent bash

# Follow all logs
logs:
	$(DC) logs -f

# Follow agent-runner logs only
logs-runner:
	$(DC) logs -f agent-runner

# Follow tracker logs only
logs-tracker:
	$(DC) logs -f tracker

# ── Auth ──────────────────────────────────────────────────────────────────────

# First-time Claude auth on a new machine (Max plan OAuth)
# Opens a browser login flow inside the container
login:
	@echo "[login] preparing volume..."
	docker run --rm \
	  -v yourproject-dev_claude-auth:/dest \
	  alpine sh -c "mkdir -p /dest && touch /dest/.claude.json && chown -R 1000:1000 /dest && chmod 700 /dest && chmod 600 /dest/.claude.json"
	@echo ""
	@echo "  Run /login inside Claude, complete OAuth in browser, then /exit"
	@echo "  If remote server: open SSH tunnel first — ssh -L 9999:localhost:9999 user@server"
	@echo ""
	$(DC) run --rm -it \
	  --entrypoint="" \
	  -p 9999:9999 \
	  agent-runner \
	  sh -c "ln -sf /home/agent/.claude/.claude.json /home/agent/.claude.json && claude --dangerously-skip-permissions"
	$(DC) restart agent-runner 2>/dev/null || true
	@echo "[login] done — tokens are in the volume via symlink"

# Local dev shortcut: copy your host ~/.claude auth into the named volume
import-auth:
	docker run --rm \
	  -v yourproject-dev_claude-auth:/dest \
	  -v ~/.claude:/src:ro \
	  -v ~/.claude.json:/claude.json:ro \
	  alpine sh -c "cp -rp /src/. /dest/ && cp /claude.json /dest/.claude.json && chown -R 1000:1000 /dest/ && chmod -R u+rwX,go+rX /dest/ && echo 'Auth imported.'"

# Fix permissions on existing claude-auth volume (run if 401 errors appear)
fix-auth-perms:
	docker run --rm \
	  -v yourproject-dev_claude-auth:/dest \
	  alpine sh -c "chown -R 1000:1000 /dest/ && chmod -R u+rwX,go+rX /dest/ && echo 'Permissions fixed.'"

# ── Build ─────────────────────────────────────────────────────────────────────

build:
	$(DC) build

rebuild:
	$(DC) build --no-cache

# ── Utils ─────────────────────────────────────────────────────────────────────

ps:
	$(DC) ps

# Delete workspace volume — will re-clone repo on next start
reset-workspace:
	$(DC) down
	docker volume rm yourproject-dev_workspace || true
	@echo "Workspace volume deleted. Run 'make agent' to re-clone."
