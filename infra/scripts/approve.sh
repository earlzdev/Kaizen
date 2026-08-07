#!/usr/bin/env bash
# =============================================================================
# Approve pending agents — scripts/approve.sh
# =============================================================================
# WHAT: Lists agents waiting to connect (Кая, …) and prompts you in the
#       terminal to approve each. Approved agents receive their token and connect
#       automatically. Works with closed ports (talks to Brain via the container).
#
# HOW:
#   ./scripts/approve.sh        (or: make approve)
# Run it after `make up`/`make dev`; re-run any time a new agent is waiting.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "Looking for agents waiting to connect…"
list=""
ready=0
for i in $(seq 1 60); do          # up to ~2 min: Brain must migrate on first boot
  set +e
  list="$(docker compose --env-file .env -f deploy/docker-compose.yml exec -T brain python -m brain.enroll_cli list 2>/dev/null)"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    ready=1
    # Brain is ready. Pending found -> go approve. Empty -> give a just-booting
    # agent a few polls to enroll before concluding there's nothing.
    [ -n "${list//[$'\t\r\n ']/}" ] && break
    [ "$i" -gt 10 ] && break
  else
    [ $((i % 5)) -eq 0 ] && echo "  …waiting for Brain to finish starting (migrations)"
  fi
  sleep 2
done

if [ "$ready" -eq 0 ]; then
  echo "Brain didn't become ready — check its logs: docker compose logs brain"
  exit 1
fi

if [ -z "${list//[$'\t\r\n ']/}" ]; then
  echo "Nothing to approve — agents that are already connected won't appear here."
  echo "(If one is still starting or newly added, run 'make approve' again in a moment.)"
  exit 0
fi

while IFS=$'\t' read -r id slug; do
  [ -z "${id:-}" ] && continue
  read -r -p "Approve agent '$slug' to connect? [y/N] " ans < /dev/tty
  if [[ "$ans" =~ ^[Yy] ]]; then
    docker compose --env-file .env -f deploy/docker-compose.yml exec -T brain python -m brain.enroll_cli approve "$id" >/dev/null
    echo "  ✓ approved '$slug' — it will connect shortly."
  else
    docker compose --env-file .env -f deploy/docker-compose.yml exec -T brain python -m brain.enroll_cli reject "$id" >/dev/null
    echo "  ✗ rejected '$slug'."
  fi
done <<< "$list"
