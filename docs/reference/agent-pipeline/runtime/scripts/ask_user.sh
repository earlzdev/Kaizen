#!/usr/bin/env bash
# Usage: ask_user.sh "Your question here"
# Sends a question to the user via Telegram and blocks until an answer is received.
# Timeout: 5 minutes. Returns the user's answer to stdout.

set -euo pipefail

TRACKER_URL="${TRACKER_URL:-http://localhost:3333}"
SESSION_ID="${RESEARCH_SESSION_ID:-default}"
QUESTION="${1:-}"

if [[ -z "$QUESTION" ]]; then
  echo "Usage: $0 <question>" >&2
  exit 1
fi

# Post question to tracker → Telegram
ESCAPED_Q=$(printf '%s' "$QUESTION" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))')
RESPONSE=$(curl -sf -X POST "${TRACKER_URL}/api/research/ask" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"${SESSION_ID}\", \"question\": ${ESCAPED_Q}}")

QA_ID=$(echo "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# Poll for answer (5 min timeout, check every 3 seconds)
TIMEOUT=300
ELAPSED=0
while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
  ANSWER_RESP=$(curl -sf "${TRACKER_URL}/api/research/answer/${QA_ID}" 2>/dev/null || echo "")
  if [ -n "$ANSWER_RESP" ]; then
    ANSWER=$(echo "$ANSWER_RESP" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("answer",""))' 2>/dev/null || echo "")
    if [ -n "$ANSWER" ]; then
      echo "$ANSWER"
      exit 0
    fi
  fi
  sleep 3
  ELAPSED=$((ELAPSED + 3))
done

echo "(User did not respond within 5 minutes)"
