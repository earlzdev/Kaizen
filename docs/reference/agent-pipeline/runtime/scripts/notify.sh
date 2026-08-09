#!/usr/bin/env bash
# Usage: scripts/notify.sh "Your message here"
# Agent calls this to send a Telegram notification via the tracker service.

set -euo pipefail

TRACKER_URL="${TRACKER_URL:-http://localhost:3333}"
MESSAGE="${1:-}"

if [[ -z "$MESSAGE" ]]; then
  echo "Usage: $0 <message>" >&2
  exit 1
fi

curl -sf -X POST "${TRACKER_URL}/api/notify" \
  -H "Content-Type: application/json" \
  -d "{\"message\": $(echo "$MESSAGE" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))')}" \
  | python3 -m json.tool || true
